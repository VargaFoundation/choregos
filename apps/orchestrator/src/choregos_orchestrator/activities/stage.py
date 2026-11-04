# SPDX-License-Identifier: Apache-2.0
"""The preparation of an agent step: model, key, context, `StageInput`.

Each function is a Temporal activity; all are idempotent by `run_id`. The launch and the wait
live in `execution`, the outcome in `outcome`, the guard against injection in `injection_guard`;
this module re-exports them, and the interpreter and the tests keep reading them here.
"""

from __future__ import annotations

import asyncio
from hashlib import sha256
from typing import Any

import structlog
from choregos_api.db.models import Run
from choregos_api.services import persist_event
from choregos_api.services.artifacts import producing_runs
from choregos_api.services.hosted_builds import builds_for
from choregos_api.services.model_catalogue import load_policy
from choregos_api.services.receipts import READ, read_by_the_prompt, record
from choregos_contracts import (
    AgentRef,
    ApiFormat,
    Budget,
    Callbacks,
    ContextPack,
    EventType,
    HostedCall,
    LaunchSpec,
    PlaybookRef,
    ProjectRef,
    RepoRef,
    StageInput,
    StageMemory,
    StageRole,
    ToolsRef,
    TransitionRef,
    WorkItemLinks,
    WorkItemRef,
)
from choregos_core import (
    ModelResolutionError,
    ModelResolver,
    telemetry,
    utcnow,
)
from choregos_core.memory_bindings import merge_context_packs
from choregos_core.redaction import REDACTED
from choregos_core.workspace import write_paths_for
from temporalio import activity

from ..config import get_settings
from ..context_reduction import forced_context, resolve_context, stage_input_payload
from ..org_egress import org_runner_hosts
from .base import db, load_work_item, org_memory, project_bundle, without_secrets
from .execution import (
    QUEUE_MAX_MINUTES,
    await_run,
    cancel_run,
    runner_environment,
    runtime_class,
    start_run,
)
from .images import named_image
from .injection_guard import guard_against_injection
from .job import NO_MODEL, prepare_job
from .outcome import collect_spend, record_run_outcome
from .plan import StagePlan
from .remote import A2A_BACKEND, a2a_runtime, check_remote_agent, remote_budget, remote_call
from .run_key import reserve_key
from .run_network import admit_network, network_permissions, run_network
from .stage_backend import (
    CODE_BACKEND,
    HOSTED_BACKEND,
    PROMPT_BACKEND,
    STEPLESS_RUNTIMES,
    _agent_plan,
    _backend_row,
    _code_runtime,
    _fixed_backend,
    _hosted_call,
    _hosted_image,
    _prompt_call,
    _require_backend_allowed,
    _require_backend_enabled,
    _reviewer_backend,
)
from .stage_refusals import (
    _admettre,
    _daily_budget,
    _refuse_on_budget,
    _require_llm_guard,
    _reservation,
    _stage_cap,
)
from .stage_tools import (
    MEMORY_DISABLED,
    ResolvedMemories,
    _agent_skills,
    _gateway_catalogue,
    _knowledge_of_agent,
    _mcp_servers,
    _memory_bindings,
    _skill_exists,
    production_indicator_sources,
)

__all__ = [
    "CODE_BACKEND",
    "HOSTED_BACKEND",
    "MEMORY_DISABLED",
    "PROMPT_BACKEND",
    "QUEUE_MAX_MINUTES",
    "STEPLESS_RUNTIMES",
    "ResolvedMemories",
    "StagePlan",
    "_backend_row",
    "_hosted_call",
    "_hosted_image",
    "_skill_exists",
    "await_run",
    "cancel_run",
    "collect_spend",
    "prepare_stage",
    "record_run_outcome",
    "runner_environment",
    "runtime_class",
    "start_run",
]


logger = structlog.get_logger("choregos.stage")


@activity.defn(name="prepare_stage")
async def prepare_stage(plan_data: dict[str, Any]) -> dict[str, Any]:
    """Resolves the model, reserves the key's cap, builds the context pack and the `StageInput`.

    One activity for all the preparation: the `run_id` makes it replayable. The gateway key is
    minted by `start_run`, for the run's Secret only (#523).
    """
    plan = StagePlan(**plan_data)
    settings = get_settings()
    async with db() as session:
        bundle = await project_bundle(session, plan.project_id)
        item = await load_work_item(session, plan.work_item_id)
        structlog.contextvars.bind_contextvars(
            project=bundle.slug, work_item=item.tracker_key, stage=plan.role
        )
        run_id = _run_id(plan)

        existing = await session.get(Run, run_id)
        if existing is not None and existing.stage_input:
            # No secret in a Temporal payload (#519): `start_run` mints the run token itself.
            return {
                "run_id": run_id,
                "stage_input": without_secrets(existing.stage_input, keep_gateway_key=False),
                "reused": True,
            }

        if plan.job is not None:  # a deterministic step (ADR 0056): no model, no key, no prompt
            return await prepare_job(session, bundle, item, plan, run_id, existing, settings, _admettre)

        engine = bundle.engine
        if (refused := await _stage_cap(session, bundle, item, run_id, plan)) is not None:
            return refused
        # An agent of the registry (ADR 0033): its effective version (the project's pin, overrides
        # applied) sets model, backend, limits and budget, and its instructions replace the
        # playbook. A revoked, suspended or expired agent does not start.
        # A trial (S32-03) runs a draft version, unpublished, in the agent's place: no daily budget
        # of a registry agent applies to it, its own cap does (below).
        agent = _trial_agent(plan) or await _plan_agent(session, bundle, plan)
        if agent is not None and plan.trial_spec is None:
            await _daily_budget(session, bundle, agent)
        # ADR 0070: an agent that needs a host its project or the organisation does not allow does
        # not start — before any key is reserved or minted. What the organisation opened for its
        # runners (S39-22d) joins the run as implied hosts; every deny list still wins.
        # The workspace the agent declares (ADR 0119): `none` clones nothing and opens no repository
        # host, `read` clones what it cannot change, `write` (the default) is today's run.
        admit_network(
            bundle,
            agent,
            settings,
            org_hosts := await org_runner_hosts(session, bundle.project.org_id),
            with_repo=(workspace := agent.spec.effective_workspace() if agent is not None else "write")
            != "none",
        )
        if agent is not None:
            plan = _agent_plan(plan, agent.spec)
        # The runtime that carries the step, if not an ACP agent. `_hosted_call` refuses here, before
        # any budget is reserved, a hosted image without a digest or a build not ready (ADR 0080).
        # A zip version's build is read from its row (S41-08): the version keeps the `pending` it
        # was published with.
        builds = await builds_for(session, bundle.project.org_id, [agent.spec]) if agent is not None else {}
        code_runtime, remote_runtime, hosted = (
            _code_runtime(agent),
            a2a_runtime(agent),
            _hosted_call(agent, builds),
        )
        # Before any budget is reserved: a remote agent the project cannot reach, or whose
        # credential is gone, never gets a reservation or a pod (ADR 0062).
        await check_remote_agent(
            session,
            bundle,
            remote_runtime,
            deny=run_network(bundle, None, settings).deny,
            org_hosts=org_hosts,
        )
        # Before any budget is reserved: an image the operator did not name never reaches a pod.
        agent_image = named_image(settings, "agent", code_runtime.image) if code_runtime else None
        # The organisation's model catalogue (ADR 0060): a model it disabled after the agent was
        # published stops the stage here, before any key is minted.
        resolver = ModelResolver(
            # The gateway the key is minted on (ADR 0109): a connector's, else the deployment's.
            gateway_url=getattr(bundle, "gateway_url", None) or settings.gateway_url,
            gateway_models=await _gateway_catalogue(bundle),
            catalogue=await load_policy(session, bundle.project.org_id),
        )
        backend = plan.backend or bundle.config.agent.default_backend
        if remote_runtime is not None:
            # A remote agent brings its own model: nothing to resolve, no model catalogue to consult
            # (S39-07), no gateway key (ADR 0062).
            backend = A2A_BACKEND
            resolved = None
            budget, reserved_usd = remote_budget(remote_runtime, plan.max_minutes)
        else:
            # A runtime fixes its backend: the cross-backend review swaps only coding agents.
            if (
                not _fixed_backend(agent)
                and plan.role == str(StageRole.REVIEW)
                and engine.cross_backend_review()
            ):
                backend = await _reviewer_backend(session, bundle, item, backend, resolver, plan)
            # S42-07c: the platform administrator's switch (`PUT /platform/backends`) is applied
            # here, at stage start, before any budget, key or pod. A stage already running is not
            # touched. A runtime fixes its own backend (`code`): the switch does not concern it.
            resolved = await _resolve_on_enabled_backend(
                session, agent, resolver, plan, bundle, item, backend
            )
            if code_runtime is not None:
                resolved.api_format = ApiFormat(code_runtime.api_format)
            budget = _stage_budget(engine, plan, item, resolved)
            reserved_usd = budget.usd
        if plan.trial_budget_usd is not None:
            # A trial's cap only tightens: the gateway key is minted at this amount (S32-03).
            budget = Budget(
                usd=min(budget.usd, plan.trial_budget_usd),
                max_turns=budget.max_turns,
                max_minutes=budget.max_minutes,
            )
            reserved_usd = min(reserved_usd, plan.trial_budget_usd)
        reservation = await _reservation(
            session, bundle, item, run_id, agent, reserved_usd, trial=plan.trial_spec is not None
        )
        # A remote agent's declared cost must fit under every cap (a free one always starts); any
        # other run needs the floor, and its budget becomes what is left.
        if remote_runtime is not None and reservation.usd < reserved_usd:
            return await _refuse_on_budget(session, bundle, item, run_id, plan, reservation)
        if remote_runtime is None:
            if reservation.below(settings.min_run_reservation_usd):
                return await _refuse_on_budget(session, bundle, item, run_id, plan, reservation)
            budget = Budget(usd=reservation.usd, max_turns=budget.max_turns, max_minutes=budget.max_minutes)

        await _admettre(bundle, item, run_id, plan, backend, reserved_usd)

        # The key itself is minted by `start_run`, which hands it to the run's Secret: no row and
        # no Temporal payload ever holds it (#523). Its cap is reserved here, where the budget
        # is decided, so that another run cannot take it in between; `start_run` turns this row
        # into the key's. Replayable: the row is found again by its run (ADR 0008).
        # A remote agent mints none, but the reservation row is what holds its declared cost against
        # the ticket and the day, and `release_run_key` closes it like any other.
        await reserve_key(
            session, bundle, run_id, _key_budget(budget, reserved_usd, remote=remote_runtime is not None)
        )

        resolved_memories, context_pack = await _memories_and_pack(
            session, bundle, item, plan, agent, remote=remote_runtime is not None
        )
        memory_scope = resolved_memories.legacy_scope or "none"
        await guard_against_injection(session, bundle, item, run_id, plan, context_pack)
        playbook_prompt = _render_playbook(plan, bundle, item, context_pack, agent)
        playbook_ref = _playbook_ref(plan, agent, playbook_prompt)
        # A `read` agent writes no repository file: its scope matches only its deliverables (ADR 0119).
        allowed_paths = write_paths_for(workspace, list(item.allowed_paths or []))
        repo_config = bundle.config.repo
        repo = _repo_ref(bundle, item, plan, workspace, remote=remote_runtime is not None, hosted=hosted)
        stage_input = StageInput(
            run_id=run_id,
            # The stage span's context, derived from the run id (ADR 0018): the runner's span hangs off it.
            trace_parent=telemetry.traceparent(run_id),
            attempt=plan.attempt,
            project=ProjectRef(
                slug=bundle.slug,
                org=bundle.org_slug,
                test_command=repo_config.test_command if repo_config else "",
                lint_command=repo_config.lint_command if repo_config else "",
                typecheck_command=repo_config.typecheck_command if repo_config else "",
                # The fact commands come from the ROOT of the configuration, not from `repo`: a
                # project without a repository must be able to prove something (ADR 0012).
                fact_commands=dict(bundle.config.dod.facts)
                if plan.eval_cell is None
                else dict(plan.eval_cell["facts"]),
            ),
            work_item=WorkItemRef(
                key=item.tracker_key,
                title=item.title,
                body=item.body_snapshot or "",
                url=item.url,
                size=item.size,
                risk=item.risk,
                links=WorkItemLinks(
                    spec_comment=(item.documents or {}).get("spec_comment"),
                    plan_comment=(item.documents or {}).get("plan_comment"),
                    pr=item.pr_url,
                ),
            ),
            transition=TransitionRef(
                id=plan.transition_id,
                role=plan.role,
                **{"from": plan.from_state},
                to=plan.to_state,
                outputs=plan.outputs or [],
                inputs=plan.inputs or [],
                output_types=plan.output_types or {},
            ),
            repo=repo,
            agent=AgentRef(
                backend=backend,
                launch=LaunchSpec(command=list(code_runtime.command)) if code_runtime else LaunchSpec(),
                image=agent_image,
            ),
            model=resolved.to_ref() if resolved is not None else NO_MODEL,
            remote=remote_call(remote_runtime, agent.agent.slug, budget)
            if remote_runtime is not None
            else None,
            prompt=_prompt_call(agent),
            hosted=hosted,
            # In the run's Secret, never here (#523): see `start_run`.
            gateway_key=None,
            budget=budget,
            allowed_paths=allowed_paths,
            context_pack_url=f"{settings.object_store_url}/runs/{run_id}/context.json",
            playbook=PlaybookRef(ref=playbook_ref, prompt=playbook_prompt),
            tools=ToolsRef(mcp={} if remote_runtime is not None else _mcp_servers(settings, memory_scope)),
            memory_scope=memory_scope,
            memories=resolved_memories.memories,
            # The container of a hosted agent reads no file of the runner's: skills would be lost on it.
            skills=[]
            if remote_runtime is not None or hosted is not None
            else await _agent_skills(session, bundle, agent),
            knowledge=[]
            if remote_runtime is not None
            else await _knowledge_of_agent(session, bundle, agent, item),
            permissions=network_permissions(
                run_network(
                    bundle,
                    agent.spec.network if agent is not None else None,
                    settings,
                    org_hosts,
                    # A run that clones nothing reaches no repository host (ADR 0119).
                    with_repo=repo is not None,
                ),
                write_paths=allowed_paths,
                deny_commands=engine.deny_commands(),
                dod_iterations=engine.dod_iterations(),
                max_findings=engine.max_findings_per_run(),
                unknown_requests=bundle.policy.sandbox.unknown_requests,
            ),
            # An eval cell runs with the reduction it measures (S37-10), never past the kill switch.
            context=resolve_context(
                bundle.policy.context,
                plan.role,
                allowed_by_deployment=settings.context_reduction,
            )
            if plan.eval_cell is None
            else forced_context(plan.eval_cell["context"], allowed_by_deployment=settings.context_reduction),
            # The run token is minted by `start_run`, for the run's Secret only: neither this row
            # nor a Temporal payload holds it (#519).
            callbacks=Callbacks(api_url=f"{settings.callback_url}/api/v1/internal", run_token=REDACTED),
        )

        payload = stage_input_payload(stage_input)
        run = existing or Run(id=run_id, work_item_id=item.id, project_id=bundle.project.id)
        run.transition_id = plan.transition_id
        run.stage_role = plan.role
        run.attempt = plan.attempt
        run.actor = plan.actor
        run.backend = backend
        run.model = resolved.litellm_model if resolved is not None else f"a2a:{agent.agent.slug}"
        run.status = "queued"
        run.stage_input = payload
        run.context_pack = context_pack.model_dump(mode="json", by_alias=True)
        run.context_pack_url = stage_input.context_pack_url
        run.playbook_checksum = stage_input.playbook.ref
        # A trial is no agent's run: it counts in no registry agent's metrics (S32-03).
        registre = agent is not None and plan.trial_spec is None
        run.agent_slug = agent.agent.slug if registre else None
        run.agent_version = agent.version if registre else None
        run.allowed_paths = allowed_paths
        run.started_at = utcnow()
        if existing is None:
            session.add(run)
        await session.flush()
        await _journal_skipped_memories(session, run_id, resolved_memories.skipped)
        # What this run's prompt carries from the earlier steps, under the fingerprint of the
        # embedded revision (S25-04). Replayable: an existing receipt is not rewritten (ADR 0008).
        # From the same reads, `_record_chain` writes the run's chain on the row (ADR 0077 §2).
        await record(
            session,
            org_id=bundle.project.org_id,
            work_item_id=item.id,
            run_id=run_id,
            stage=plan.role,
            outputs=await _record_chain(session, run, item, list(plan.inputs or []), playbook_prompt),
            genre=READ,
        )
        await persist_event(
            session,
            EventType.RUN_QUEUED,
            project_id=bundle.project.id,
            work_item_id=item.id,
            project_slug=bundle.slug,
            subject=run_id,
            run_id=run_id,
            stage=plan.role,
            backend=backend,
            model=resolved.litellm_model if resolved is not None else f"a2a:{agent.agent.slug}",
        )
        return {
            "run_id": run_id,
            "stage_input": without_secrets(payload, keep_gateway_key=False),
            "reused": False,
        }


def _repo_ref(
    bundle: Any, item: Any, plan: StagePlan, workspace: str, *, remote: bool, hosted: HostedCall | None
) -> RepoRef | None:
    """What the runner clones, or `None`: it then prepares an empty directory. A dummy repository, as
    we used to send, faked a workspace that did not exist and promised a branch nobody would read.

    A remote agent edits no repository: nothing to clone, nothing to push (ADR 0062); nor does a
    hosted one published with `workspace: none` (S41-18), nor any agent that declares
    `workspace: none` (ADR 0119)."""
    repo_config = bundle.config.repo
    if (
        not repo_config
        or workspace == "none"
        or remote
        or (hosted is not None and hosted.workspace == "none")
    ):
        return None
    return RepoRef(
        url=repo_config.url,
        base_branch=repo_config.default_branch
        if plan.eval_cell is None
        else str(plan.eval_cell["base_branch"]),
        work_branch=bundle.config.branch_for(item.tracker_key),
        clone_depth=repo_config.clone_depth,
    )


def _stage_budget(engine: Any, plan: StagePlan, item: Any, resolved: Any) -> Budget:
    """The stage's budget: the role's, tightened by what the actor says of turns and minutes."""
    budget: Budget = engine.budget_for(plan.role, item.size, turns_factor=resolved.turns_factor)
    if plan.max_turns:
        budget = Budget(usd=budget.usd, max_turns=plan.max_turns, max_minutes=budget.max_minutes)
    if plan.max_minutes:
        budget = Budget(usd=budget.usd, max_turns=budget.max_turns, max_minutes=plan.max_minutes)
    return budget


def _resolve_model(resolver: ModelResolver, plan: StagePlan, bundle: Any, item: Any, backend: str) -> Any:
    """The stage's model, or a refusal that is not retried: a model the resolver refuses — unknown
    profile, no tool calling, a backend that does not take it — stays refused until an operator
    changes the configuration. The interpreter escalates the work item on it (#543)."""
    from temporalio.exceptions import ApplicationError

    _require_llm_guard(bundle)
    try:
        return resolver.resolve(
            plan.model_request, project=bundle.config, size=item.size, backend=backend, role=plan.role
        )
    except ModelResolutionError as refusal:
        raise ApplicationError(str(refusal), type="ModelResolutionError", non_retryable=True) from refusal


async def _resolve_on_enabled_backend(
    session: Any, agent: Any, resolver: ModelResolver, plan: StagePlan, bundle: Any, item: Any, backend: str
) -> Any:
    """The model of the stage, once its backend is known to be allowed and enabled (S42-07c, S47-20)."""
    _require_backend_allowed(agent, bundle, backend)
    await _require_backend_enabled(session, agent, backend)
    return _resolve_model(resolver, plan, bundle, item, backend)


def _run_id(plan: StagePlan) -> str:
    """Deterministic identifier: replaying the activity does not create a second run."""
    return f"{plan.work_item_id}-{plan.transition_id}-{plan.attempt}"


def _playbook_ref(plan: StagePlan, agent: Any, prompt: str) -> str:
    """`agent:<slug>@<version>` (or the playbook, or the role) pinned by the prompt's digest."""
    digest = sha256(prompt.encode()).hexdigest()[:12]
    source = (
        f"agent:{agent.agent.slug}@{agent.version}" if agent is not None else (plan.playbook or plan.role)
    )
    return f"{source}@sha256:{digest}"


def _key_budget(budget: Budget, reserved_usd: float, *, remote: bool) -> Budget:
    """What the reservation row holds: the run's budget, or a remote agent's declared cost."""
    if not remote:
        return budget
    return Budget.model_construct(
        usd=reserved_usd, max_turns=budget.max_turns, max_minutes=budget.max_minutes
    )


async def _memories_and_pack(
    session: Any, bundle: Any, item: Any, plan: StagePlan, agent: Any, *, remote: bool
) -> tuple[ResolvedMemories, ContextPack]:
    """The memories this run binds and the context pack read from them. A remote agent is sent what the
    step is about, never a memory (ADR 0062 §4)."""
    if remote:
        resolved = ResolvedMemories([], [])
        return resolved, await _context_pack(bundle, item, plan, resolved, None)
    resolved = await _memory_bindings(session, bundle, agent)
    return resolved, await _context_pack(bundle, item, plan, resolved, await org_memory(session, bundle))


async def _record_chain(session: Any, run: Run, item: Any, inputs: list[str], prompt: str) -> dict[str, Any]:
    """The run's chain (ADR 0077 §2), from what the platform holds, never from the agent: who started
    the work item, and the runs that produced what this step reads. Returns what the prompt embeds."""
    embedded = read_by_the_prompt(item.documents or {}, inputs, prompt)
    run.initiated_by = item.initiated_by
    run.inputs_from = await producing_runs(session, item.id, [*inputs, *embedded], embedded, reader=run.id)
    return embedded


async def _journal_skipped_memories(session: Any, run_id: str, skipped: list[dict[str, str]]) -> None:
    """One `memory.skipped` event per unresolved binding, on the run page; once, even if replayed."""
    if not skipped:
        return
    from choregos_api.db.models import RunEvent
    from sqlalchemy import func, select

    rows = (
        await session.execute(
            select(RunEvent.payload).where(RunEvent.run_id == run_id, RunEvent.type == "memory.skipped")
        )
    ).scalars()
    already = {str(payload.get("memory")) for payload in rows}
    seq = int(
        (await session.execute(select(func.max(RunEvent.seq)).where(RunEvent.run_id == run_id))).scalar() or 0
    )
    for entry in skipped:
        if entry["memory"] in already:
            continue
        seq += 1
        session.add(RunEvent(run_id=run_id, seq=seq, type="memory.skipped", payload=dict(entry), ts=utcnow()))


async def _context_pack(
    bundle: Any, item: Any, plan: StagePlan, resolved: ResolvedMemories, org_adapter: Any = None
) -> ContextPack:
    """Memory bounded by role; a failure yields an empty pack rather than blocking the stage.

    Every memory the run reads is queried concurrently (a failing one contributes nothing, the others
    still do) and the packs are merged by precedence (S40-11); a run with the single legacy memory
    gets its pack unmerged, as before.
    """
    engine = bundle.engine
    budget_tokens = engine.memory_budget(plan.role)
    if budget_tokens <= 0 or not resolved.memories:
        return ContextPack.empty(item.title)
    kinds_by_role = {
        "refine": ["decision", "incident", "ticket_summary"],
        "plan": ["decision", "convention"],
        "implement": ["convention", "run_lesson", "hotspot"],
        "review": ["incident", "hotspot", "decision"],
        "verify": ["flaky_test", "run_lesson"],
    }
    query = f"{item.title}\n{(item.documents or {}).get('spec_markdown', '')}"[:2000]
    timeout = bundle.policy.memory.read_timeout_ms / 1000 * 10

    async def read(memory: StageMemory) -> ContextPack:
        adapter = (
            (org_adapter or bundle.adapters.memory) if memory.scope == "global" else bundle.adapters.memory
        )
        try:
            return await asyncio.wait_for(
                adapter.context_pack(
                    memory.namespace,
                    query,
                    list(item.allowed_paths or []),
                    budget_tokens,
                    kinds_by_role.get(plan.role),
                ),
                timeout=timeout,
            )
        except (TimeoutError, Exception):
            return ContextPack.empty(query)

    packs = await asyncio.gather(*(read(memory) for memory in resolved.memories))
    if len(packs) == 1 and resolved.legacy_scope is not None:
        return packs[0]
    return merge_context_packs(
        [(m.label, pack) for m, pack in zip(resolved.memories, packs, strict=True)], budget_tokens, query
    )


def _render_playbook(plan: StagePlan, bundle: Any, item: Any, context: ContextPack, agent: Any = None) -> str:
    text = _render_playbook_text(plan, bundle, item, context, agent)
    answer = str((item.documents or {}).get("human_answer") or "").strip()
    if not answer:
        return text
    # A person answered the question that had stopped the work item (#313): the agent that
    # resumes reads it first, without the workflow's author having to declare it as an input.
    return f"## A person answered the question that stopped this work item\n\n{answer}\n\n---\n\n{text}"


def _render_playbook_text(
    plan: StagePlan, bundle: Any, item: Any, context: ContextPack, agent: Any = None
) -> str:
    from choregos_playbooks import frame, render_playbook

    documents = item.documents or {}
    variables = {
        # `fields`: what the work item was born with (ADR 0031) — a child opened by a fan-out holds
        # there what its parent measured for it (ADR 0057, S38-15).
        "ticket": {
            "key": item.tracker_key,
            "title": item.title,
            # The body opens with who filed it (#1179): every agent reads it where it reads the
            # request, an installed one included, without a new version of its instructions (whose
            # checksum holds the context block they were installed with).
            "body": _body_with_its_reporter(item),
            "fields": dict(item.fields or {}),
            "reporter": getattr(item, "reporter", None) or "",
        },
        "spec": documents.get("spec_markdown", ""),
        "plan_markdown": documents.get("plan_markdown", ""),
        # The inputs the transition declares (`inputs: [profils]`), read under their name: this is
        # how a trade step receives what the previous one produced.
        "inputs": {name: documents.get(name, "") for name in (plan.inputs or [])},
        "allowed_paths": list(item.allowed_paths or []),
        "context": context,
        "project": bundle.config,
        "production_indicators": production_indicator_sources(bundle.config, agent),
    }
    if agent is not None and agent.spec.instructions.strip():
        from choregos_core.instructions import render_instructions
        from jinja2 import TemplateError
        from temporalio.exceptions import ApplicationError

        # Rendered in the sandbox, then framed: the output contract is not the author's. A
        # template that does not render (a sandbox-refused escape included) is not retried: it
        # will not render any better the fifth time.
        try:
            return frame(render_instructions(agent.spec.instructions, **variables))
        except TemplateError as error:
            who = f"{agent.agent.slug}@{agent.version}"
            raise ApplicationError(
                f"the instructions of the agent `{who}` do not render: {error}",
                type="InstructionsInvalides",  # lang: fr-ok — recorded wire value, S45-39
                non_retryable=True,
            ) from error
    return render_playbook(plan.playbook or plan.role, **variables)


def _trial_agent(plan: StagePlan) -> Any:
    """The draft version a trial runs (S32-03): it stands in for a registry agent — its
    instructions, model, backend, limits and skills — without being published."""
    if plan.trial_spec is None:
        return None
    from types import SimpleNamespace

    from choregos_api.schemas import AgentSpec

    return SimpleNamespace(
        agent=SimpleNamespace(slug="trial"), version=0, spec=AgentSpec.model_validate(plan.trial_spec)
    )


def _body_with_its_reporter(item: Any) -> str:
    """The work item's body as an agent reads it: `Filed by: <reporter>` first when it is known."""
    body = item.body_snapshot or ""
    # A stand-in work item (an eval's, a dry run's) may not carry the column.
    reporter = getattr(item, "reporter", None)
    if not reporter:
        return body
    return f"Filed by: {reporter}\n\n{body}" if body else f"Filed by: {reporter}"


async def _plan_agent(session: Any, bundle: Any, plan: StagePlan) -> Any:
    """The agent the actor names, resolved for this project; `None` if the actor names none."""
    if not plan.agent:
        return None
    from choregos_api.services.agents import AgentUnavailable, resolve_agent
    from temporalio.exceptions import ApplicationError

    try:
        # The work item decides a canary share, so every stage and retry of it gets one revision.
        resolved = await resolve_agent(
            session, bundle.project.org_id, bundle.project.id, plan.agent, work_item_id=plan.work_item_id
        )
    except AgentUnavailable as refusal:
        raise ApplicationError(
            str(refusal),
            type="AgentIndisponible",  # lang: fr-ok — recorded wire value, S45-39
            non_retryable=True,
        ) from refusal
    logger.info(
        "agent resolved",
        project=bundle.project.slug,
        work_item=plan.work_item_id,
        stage=plan.role,
        agent=resolved.agent.slug,
        agent_version=resolved.version,
        agent_source=resolved.source,
    )
    return resolved
