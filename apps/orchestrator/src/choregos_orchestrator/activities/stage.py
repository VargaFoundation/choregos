# SPDX-License-Identifier: Apache-2.0
"""La préparation d'une étape agent : modèle, clé, contexte, `StageInput`.

Chaque fonction est une activité Temporal ; toutes sont idempotentes par `run_id`. Le
lancement et l'attente vivent dans `execution`, le bilan dans `bilan`, la garde contre
l'injection dans `garde` — ce module les réexporte, l'interpréteur et les tests continuent
de les lire ici.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from choregos_api.db.models import BackendRegistryRow, Run
from choregos_api.logging import bind, get_logger
from choregos_api.services import persist_event
from choregos_api.services.artifacts import producing_runs
from choregos_api.services.hosted_builds import builds_for
from choregos_api.services.model_catalogue import load_policy
from choregos_api.services.recus import LUE, consigner, lu_par_le_prompt
from choregos_contracts import (
    AgentRef,
    ApiFormat,
    Budget,
    Callbacks,
    ContextPack,
    EventType,
    HostedCall,
    KnowledgeRef,
    LaunchSpec,
    McpServerRef,
    PlaybookRef,
    ProjectRef,
    PromptCall,
    RepoRef,
    SkillRef,
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
    backend_enabled,
    telemetry,
    utcnow,
)
from choregos_core.cross_review import reviewer_candidates
from choregos_core.memory_bindings import Binding, legacy_scope, merge_context_packs
from choregos_core.redaction import REDACTED
from sqlalchemy import select
from temporalio import activity

from ..config import get_settings
from ..context_reduction import forced_context, resolve_context, stage_input_payload
from ..org_egress import org_runner_hosts
from .base import db, load_work_item, org_memory, project_bundle, without_secrets
from .bilan import collect_spend, record_run_outcome
from .execution import (
    FILE_MAX_MINUTES,
    await_run,
    cancel_run,
    classe_d_execution,
    env_du_runner,
    start_run,
)
from .garde import garde_contre_l_injection
from .images import named_image
from .job import NO_MODEL, prepare_job
from .plan import StagePlan
from .remote import A2A_BACKEND, a2a_runtime, check_remote_agent, remote_budget, remote_call
from .run_key import reserve_key
from .run_network import admit_network, network_permissions, run_network

__all__ = [
    "FILE_MAX_MINUTES",
    "StagePlan",
    "await_run",
    "cancel_run",
    "classe_d_execution",
    "collect_spend",
    "env_du_runner",
    "prepare_stage",
    "record_run_outcome",
    "start_run",
]

logger = get_logger("choregos.stage")


def _serveurs_mcp(settings: Any, memory_scope: str = "project") -> dict[str, McpServerRef]:
    """Les serveurs MCP qu'on annonce à l'agent — ceux qui existent, et eux seuls.

    Les deux étaient écrits en dur, et les deux étaient faux sur le locataire dev du 2026-09-27 :
    le sidecar d'outils n'existe que sous l'exécuteur `tekton`, et l'URL de la mémoire était
    fabriquée par `gateway_url.replace("4000", "8432")` — un remplacement de port qui ne tient
    que si la passerelle et la mémoire partagent un hôte. Là-bas, la passerelle est
    `http://litellm:4000` : la mémoire devenait `http://litellm:8432`, mesurée sans réponse en
    huit secondes, quand `http://ecphoria:8432/mcp` répond en neuf millisecondes.
    """
    serveurs: dict[str, McpServerRef] = {}
    if url := settings.url_du_sidecar_d_outils:
        serveurs["choregos"] = McpServerRef(url=url)
    # The memory server serves the PROJECT's shared memory: a run whose bindings are anything else (its
    # own memory, none, several, a global one) does not get it — it would read and write past its
    # bindings (S32-02, S40-11).
    if settings.memory_url and memory_scope == "project":
        serveurs["memory"] = McpServerRef(url=f"{settings.memory_url.rstrip('/')}/mcp")
    return serveurs


async def _gateway_catalogue(bundle: Any) -> list[Any]:
    """The models the project's gateway declares, so that the resolver sees behind an alias.

    Without them `provider_model` stayed empty, and the runner's `claude-code` refused every run on
    `platform/*`: it only takes a model it can see is a Claude (S28-02, 2026-10-12). A gateway that
    cannot list its models degrades to the old behaviour — no catalogue, no check — rather than
    stopping the stage.
    """
    try:
        return list(await bundle.adapters.gateway.list_models())
    except Exception as error:  # the catalogue sharpens the resolution, it is not a precondition
        logger.warning("gateway catalogue unavailable", error=str(error)[:300])
        return []


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
        bind(project=bundle.slug, work_item=item.tracker_key, stage=plan.role)
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
        # Un agent du registre (ADR 0033) : sa version effective — l'épinglée du projet, surcharges
        # appliquées — fixe modèle, backend, limites et budget, et ses instructions remplacent le
        # playbook. Un agent révoqué, suspendu ou expiré ne part pas.
        # A trial (S32-03) runs a draft version, unpublished, in the agent's place: no daily budget
        # of a registry agent applies to it, its own cap does (below).
        agent = _agent_d_essai(plan) or await _agent_du_plan(session, bundle, plan)
        if agent is not None and plan.trial_spec is None:
            await _budget_du_jour(session, bundle, agent)
        # ADR 0070: an agent that needs a host its project or the organisation does not allow does
        # not start — before any key is reserved or minted. What the organisation opened for its
        # runners (S39-22d) joins the run as implied hosts; every deny list still wins.
        admit_network(
            bundle, agent, settings, org_hosts := await org_runner_hosts(session, bundle.project.org_id)
        )
        if agent is not None:
            plan = _plan_de_l_agent(plan, agent.spec)
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
        await garde_contre_l_injection(session, bundle, item, run_id, plan, context_pack)
        playbook_prompt = _render_playbook(plan, bundle, item, context_pack, agent)
        playbook_ref = _playbook_ref(plan, agent, playbook_prompt)
        allowed_paths = list(item.allowed_paths or []) or ["**"]
        documents = item.documents or {}
        depot = bundle.config.repo
        stage_input = StageInput(
            run_id=run_id,
            # The stage span's context, derived from the run id (ADR 0018): the runner's span hangs off it.
            trace_parent=telemetry.traceparent(run_id),
            attempt=plan.attempt,
            project=ProjectRef(
                slug=bundle.slug,
                org=bundle.org_slug,
                test_command=depot.test_command if depot else "",
                lint_command=depot.lint_command if depot else "",
                typecheck_command=depot.typecheck_command if depot else "",
                # Les commandes de faits viennent de la RACINE de la configuration, pas de
                # `repo` : un projet sans dépôt doit pouvoir prouver quelque chose (ADR 0012).
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
                    spec_comment=documents.get("spec_comment"),
                    plan_comment=documents.get("plan_comment"),
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
            # Pas de dépôt, pas de `repo` : le runner prépare un répertoire vide au lieu de
            # cloner. Un dépôt factice, comme on le faisait, faisait croire à un workspace
            # qui n'existait pas et promettait une branche que personne ne relirait.
            repo=RepoRef(
                url=depot.url,
                base_branch=depot.default_branch
                if plan.eval_cell is None
                else str(plan.eval_cell["base_branch"]),
                work_branch=bundle.config.branch_for(item.tracker_key),
                clone_depth=depot.clone_depth,
            )
            # A remote agent edits no repository: nothing to clone, nothing to push (ADR 0062); nor
            # does a hosted one published with `workspace: none` (S41-18).
            if depot and remote_runtime is None and (hosted is None or hosted.workspace != "none")
            else None,
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
            tools=ToolsRef(mcp={} if remote_runtime is not None else _serveurs_mcp(settings, memory_scope)),
            memory_scope=memory_scope,
            memories=resolved_memories.memories,
            # The container of a hosted agent reads no file of the runner's: skills would be lost on it.
            skills=[]
            if remote_runtime is not None or hosted is not None
            else await _skills_de_l_agent(session, bundle, agent),
            knowledge=[]
            if remote_runtime is not None
            else await _knowledge_of_agent(session, bundle, agent, item),
            permissions=network_permissions(
                run_network(bundle, agent.spec.network if agent is not None else None, settings, org_hosts),
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
        # Ce que le prompt de ce run embarque des étapes d'avant, sous l'empreinte de la révision
        # embarquée (S25-04). Rejouable : un reçu existant ne se réécrit pas (ADR 0008).
        # From the same reads, `_record_chain` writes the run's chain on the row (ADR 0077 §2).
        await consigner(
            session,
            org_id=bundle.project.org_id,
            work_item_id=item.id,
            run_id=run_id,
            stage=plan.role,
            sorties=await _record_chain(session, run, item, list(plan.inputs or []), playbook_prompt),
            genre=LUE,
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


def _require_llm_guard(bundle: Any) -> None:
    """A policy that masks secrets on the LLM path needs a gateway that can (ADR 0084): refused
    before the budget is reserved, and escalated like a model the project cannot use — the
    configuration, not the run, has to change."""
    from choregos_adapters.base import llm_guard_refusal
    from temporalio.exceptions import ApplicationError

    if not getattr(bundle.policy.sandbox, "llm_security_analyzer", False):
        return
    if (refusal := llm_guard_refusal(bundle.adapters.gateway)) is not None:
        raise ApplicationError(refusal, type="ModelResolutionError", non_retryable=True)


async def _admettre(
    bundle: Any, item: Any, run_id: str, plan: StagePlan, backend: str, budget_usd: float
) -> None:
    """Les admissions déclarées par les greffons (`choregos_core.admission`), avant toute clé.

    Un refus arrête l'étape SANS reprise, comme la garde contre l'injection : retenter ne changerait
    rien tant que la règle tient, et le ticket est marqué mort avec la raison, qu'un humain lit.
    """
    from choregos_core.admission import AdmissionRefusee, DemandeDeRun, verifier
    from temporalio.exceptions import ApplicationError

    demande = DemandeDeRun(
        org=bundle.org_slug,
        project=bundle.slug,
        work_item=item.tracker_key,
        run_id=run_id,
        role=plan.role,
        backend=backend,
        budget_usd=budget_usd,
    )
    try:
        await verifier(demande)
    except AdmissionRefusee as refus:
        raise ApplicationError(
            f"run refused before it started — {refus}", type="admission_refused", non_retryable=True
        ) from refus


async def _reservation(
    session: Any,
    bundle: Any,
    item: Any,
    run_id: str,
    agent: Any,
    stage_usd: float,
    *,
    trial: bool = False,
) -> Any:
    """What this run may spend (ADR 0047): the smallest of what is left under the stage budget, the
    agent's run, day and month budgets, the ticket budget, the project's day budget and the
    organisation's month budget. What is left under a scope is its cap, minus the ledger, minus the
    keys still open at that scope (S27-11). A trial has no day or month budget: its runs belong to
    no agent, so no spend is counted against one (S32-03).

    Serialized per scope: the scopes this stage reserves from are locked until the transaction ends
    (`lock_budget_scopes`), so a second stage reads the first one's reservation instead of the same
    remainder."""
    from choregos_api.db.models import Organization
    from choregos_api.services.agents import depense_du_jour, depense_du_mois
    from choregos_api.services.budgets import (
        lock_budget_scopes,
        open_reservations,
        org_spent_since,
        project_spent_since,
        reserve,
        start_of_day,
        start_of_month,
        ticket_budget,
        ticket_spent,
    )
    from choregos_api.services.definitions import ceiling_of

    engine = bundle.engine
    org_id = bundle.project.org_id
    org_cap = None
    if (ceiling := ceiling_of(await session.get(Organization, org_id))) is not None:
        org_cap = ceiling.budgets.monthly_org_usd
    agent_slug = agent.agent.slug if agent is not None and not trial else None
    agent_capped = (
        agent is not None
        and not trial
        and (agent.spec.budget.daily_usd is not None or agent.spec.budget.monthly_usd is not None)
    )
    scopes = [f"ticket:{item.id}"]
    if engine.daily_budget() is not None:
        scopes.append(f"project:{bundle.project.id}")
    if org_cap is not None:
        scopes.append(f"org:{org_id}")
    if agent_capped:
        scopes.append(f"agent:{org_id}:{agent_slug}")
    await lock_budget_scopes(session, scopes)

    left: dict[str, float | None] = {"stage": stage_usd}
    if agent is not None:
        left["agent_run"] = agent.spec.budget.run_usd
        if agent.spec.budget.daily_usd is not None and not trial:
            spent = await depense_du_jour(session, org_id, agent.agent.slug)
            held = await open_reservations(
                session, org_id=org_id, agent_slug=agent.agent.slug, exclude_run=run_id
            )
            left["agent_day"] = agent.spec.budget.daily_usd - spent - held
        if agent.spec.budget.monthly_usd is not None and not trial:
            # The monthly cap is a reservation too (S27-09): a run cannot spend past it, nor can
            # the agent's runs in flight, each of which may still spend its key's cap (S27-11).
            month = await depense_du_mois(session, org_id, agent.agent.slug)
            held = await open_reservations(
                session, org_id=org_id, agent_slug=agent.agent.slug, exclude_run=run_id
            )
            left["agent_month"] = agent.spec.budget.monthly_usd - month - held
    left["ticket"] = (
        ticket_budget(engine, item)
        - await ticket_spent(session, item.id)
        - await open_reservations(session, work_item_id=item.id, exclude_run=run_id)
    )
    if (day := engine.daily_budget()) is not None:
        left["project_day"] = (
            day
            - await project_spent_since(session, bundle.project.id, start_of_day())
            - await open_reservations(session, project_id=bundle.project.id, exclude_run=run_id)
        )
    if org_cap is not None:
        left["org_month"] = (
            org_cap
            - await org_spent_since(session, org_id, start_of_month())
            - await open_reservations(session, org_id=org_id, exclude_run=run_id)
        )
    return reserve(left)


async def _stage_cap(
    session: Any, bundle: Any, item: Any, run_id: str, plan: StagePlan
) -> dict[str, Any] | None:
    """The work item ran every agent stage the policy allows (`attempts.max_stages_per_ticket`,
    S27-08): no key, no run, a person decides. Counted from the runs table, so a restart, a replay or
    a resume does not reset it."""
    from sqlalchemy import func

    cap = bundle.engine.max_stages_per_ticket()
    ran = (
        await session.execute(
            select(func.count()).select_from(Run).where(Run.work_item_id == item.id, Run.id != run_id)
        )
    ).scalar_one()
    if ran < cap:
        return None
    logger.info("stage refused: stage cap", run_id=run_id, ran=ran, cap=cap)
    detail = (
        f"the stage `{plan.role}` did not start: the work item already ran {ran} agent stages, "
        f"the policy's max_stages_per_ticket is {cap}"
    )
    return {"run_id": run_id, "refused": {"reason": "max_stages", "detail": detail}}


async def _resolve_on_enabled_backend(
    session: Any, agent: Any, resolver: ModelResolver, plan: StagePlan, bundle: Any, item: Any, backend: str
) -> Any:
    """The model of the stage, once its backend is known to be allowed and enabled (S42-07c, S47-20)."""
    _require_backend_allowed(agent, bundle, backend)
    await _require_backend_enabled(session, agent, backend)
    return _resolve_model(resolver, plan, bundle, item, backend)


async def _backend_row(session: Any, backend: str) -> Any:
    return (
        await session.execute(select(BackendRegistryRow).where(BackendRegistryRow.name == backend))
    ).scalar_one_or_none()


def _require_backend_allowed(agent: Any, bundle: Any, backend: str) -> None:
    """Refuse the stage when the project's `agent.allowed_backends` does not list its backend.

    An empty list restricts nothing. A runtime that fixes its own backend is not concerned. The
    refusal has the same typed, non retryable shape as a backend the platform disabled."""
    from temporalio.exceptions import ApplicationError

    if _fixed_backend(agent):
        return
    allowed = list(bundle.config.agent.allowed_backends)
    if not allowed or backend in allowed:
        return
    logger.info("stage refused: backend not allowed", backend=backend, allowed=allowed)
    raise ApplicationError(
        f"the agent backend `{backend}` is not one this project allows ({', '.join(allowed)}): "
        "add it to the project's agent settings, or choose another backend.",
        type="BackendDisabled",
        non_retryable=True,
    )


async def _require_backend_enabled(session: Any, agent: Any, backend: str) -> None:
    """Refuse the stage when the platform administrator disabled its coding-agent backend.

    Read from the `agent_backends` table inside the activity (ADR 0008: a replay reads it again
    and a stage that already started is never re-checked). No row means nothing was ever
    switched off: the backend stays enabled (fresh install). The refusal is a typed, non
    retryable error the interpreter escalates as a configuration refusal."""
    from temporalio.exceptions import ApplicationError

    if _fixed_backend(agent):
        return
    row = await _backend_row(session, backend)
    if backend_enabled(backend, row.enabled if row else None):
        return
    if row is None:
        raise ApplicationError(
            f"the agent backend `{backend}` is disabled by the platform administrator (it was never "
            "enabled on the platform page). Enable it there, or choose another backend.",
            type="BackendDisabled",
            non_retryable=True,
        )
    reason = f" Reason given: {row.disabled_reason}." if row.disabled_reason else ""
    logger.info("stage refused: backend disabled", backend=backend)
    raise ApplicationError(
        f"the agent backend `{backend}` is disabled by the platform administrator.{reason} "
        "Enable it on the platform page, or choose another backend.",
        type="BackendDisabled",
        non_retryable=True,
    )


async def _refuse_on_budget(
    session: Any, bundle: Any, item: Any, run_id: str, plan: StagePlan, reservation: Any
) -> dict[str, Any]:
    """Not enough left to start: no key, no run. The interpreter sends the ticket to a person."""
    detail = f"the stage `{plan.role}` did not start: {reservation.describe()}"
    logger.info("stage refused on budget", run_id=run_id, bound_by=reservation.bound_by)
    await persist_event(
        session,
        EventType.COST_ALERT,
        project_id=bundle.project.id,
        work_item_id=item.id,
        project_slug=bundle.slug,
        subject=item.tracker_key,
        reason="budget",
        stage=plan.role,
        bound_by=reservation.bound_by,
        left=reservation.left,
    )
    return {"run_id": run_id, "refused": {"reason": "budget", "detail": detail}}


def _run_id(plan: StagePlan) -> str:
    """Identifiant déterministe : rejouer l'activité ne crée pas un second run."""
    return f"{plan.work_item_id}-{plan.transition_id}-{plan.attempt}"


@dataclass(slots=True)
class ResolvedMemories:
    """The memories a run reads and writes, resolved for its project (S40-11, ADR 0075)."""

    memories: list[StageMemory]
    #: Bindings that did not resolve: `{memory, reason}`; the run starts without them (ADR 0007).
    skipped: list[dict[str, str]]

    @property
    def legacy_scope(self) -> str | None:
        return legacy_scope([Binding(m.scope, m.slug, m.mode) for m in self.memories])


async def _memory_bindings(session: Any, bundle: Any, agent: Any) -> ResolvedMemories:
    """Turn the agent version's effective bindings into the memories THIS run reads and writes.

    A global memory must exist, be active and reach the run's project; a named project memory must
    exist and be active in the run's project; `shared` and `own` always resolve (`own` needs an agent).
    What does not resolve is skipped with its reason, never fatal: memory never blocks a stage
    (ADR 0007). A playbook run, with no agent, is `[project:shared write]`, as before.
    """
    from choregos_api.db.models import MemoryStore
    from choregos_api.services.memory_access import reaches_project
    from choregos_core.memory_bindings import OWN, SHARED, effective_bindings, namespace_of_binding
    from sqlalchemy import select

    spec = getattr(agent, "spec", None)
    bindings = (
        effective_bindings(None, "project")
        if spec is None
        else [Binding(b.scope, b.slug, b.mode) for b in spec.effective_memories()]
    )
    agent_slug = agent.agent.slug if agent is not None else None
    stores = {
        (store.scope, store.owner_key, store.slug): store
        for store in (
            await session.execute(select(MemoryStore).where(MemoryStore.org_id == bundle.project.org_id))
        ).scalars()
    }
    resolved: list[StageMemory] = []
    skipped: list[dict[str, str]] = []
    for binding in bindings:
        reason: str | None = None
        if binding.scope == "global":
            store = stores.get(("global", "org", binding.slug))
            if store is None:
                reason = "the memory does not exist"
            elif store.status != "active":
                reason = "the memory is archived"
            elif not reaches_project(store, bundle.slug):
                reason = f"the memory does not reach project {bundle.slug}"
        elif binding.slug == OWN and not agent_slug:
            reason = "a playbook run has no agent, so no memory of its own"
        elif binding.slug not in (SHARED, OWN):
            store = stores.get(("project", str(bundle.project.id), binding.slug))
            if store is None:
                reason = f"the project has no memory `{binding.slug}`"
            elif store.status != "active":
                reason = "the memory is archived"
        if reason is not None:
            skipped.append({"memory": binding.label, "reason": reason})
            continue
        namespace = namespace_of_binding(
            bundle.org_memory_key or bundle.org_slug, bundle.slug, agent_slug, binding
        )
        if namespace is None:  # unreachable: `own` without an agent is refused above
            continue
        resolved.append(
            StageMemory(
                label=binding.label,
                scope=binding.scope,
                slug=binding.slug,
                mode=binding.mode,
                namespace=namespace,
            )
        )
    return ResolvedMemories(resolved, skipped)


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
    embedded = lu_par_le_prompt(item.documents or {}, inputs, prompt)
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


#: What the name of an agent's MCP server must contain to count as a production indicator source (the
#: same cue as the `devops` agent: Loki, Prometheus, Grafana...).
_OBSERVABILITY_MCP_NAMES = ("grafana", "prometheus", "loki", "datadog", "sentry", "newrelic", "honeycomb")


def production_indicator_sources(config: Any, agent: Any = None) -> list[str]:
    """The production indicator sources THIS run can read, by name (S22-48).

    The production verifier demanded before/after figures for errors and latency, and a project with
    no source of them (the dev tenant: demo CD, no metrics) was refused every time, with smoke_ok
    measured true. Its instructions now say what to demand from what the project can measure; the
    platform tells it, the agent does not guess.

    Two sources, the ones a run actually reaches: the deployment catalogue's tools the project may
    call and that the deployment files under `observability`; the agent version's MCP servers named
    after an observability tool. Empty = none.
    """
    from choregos_api.catalogue import outils_du_projet

    sources = {
        tool.name
        for tool in outils_du_projet(list(config.tools or []), list(config.groups or []))
        if "observability" in tool.categories
    }
    servers = getattr(getattr(agent, "spec", None), "mcp_servers", None) or []
    sources |= {
        s.connector for s in servers if any(m in s.connector.lower() for m in _OBSERVABILITY_MCP_NAMES)
    }
    return sorted(sources)


def _render_playbook(plan: StagePlan, bundle: Any, item: Any, context: ContextPack, agent: Any = None) -> str:
    texte = _rendre_le_playbook(plan, bundle, item, context, agent)
    reponse = str((item.documents or {}).get("human_answer") or "").strip()
    if not reponse:
        return texte
    # Une personne a répondu à la question qui avait arrêté le ticket (#313) : l'agent qui reprend
    # la lit en tête, sans que l'auteur du workflow ait eu à la déclarer en entrée.
    return f"## A person answered the question that stopped this work item\n\n{reponse}\n\n---\n\n{texte}"


def _rendre_le_playbook(
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
            "body": item.body_snapshot or "",
            "fields": dict(item.fields or {}),
        },
        "spec": documents.get("spec_markdown", ""),
        "plan_markdown": documents.get("plan_markdown", ""),
        # Les entrées que la transition déclare (`inputs: [profils]`), lues sous leur nom :
        # c'est ainsi qu'une étape métier reçoit ce que la précédente a produit.
        "inputs": {nom: documents.get(nom, "") for nom in (plan.inputs or [])},
        "allowed_paths": list(item.allowed_paths or []),
        "context": context,
        "project": bundle.config,
        "production_indicators": production_indicator_sources(bundle.config, agent),
    }
    if agent is not None and agent.spec.instructions.strip():
        from choregos_core.instructions import rendre_les_instructions
        from jinja2 import TemplateError
        from temporalio.exceptions import ApplicationError

        # Rendues dans le bac à sable, puis cadrées : le contrat de sortie n'est pas à l'auteur. Un
        # gabarit qui ne rend pas — une évasion refusée par le bac à sable comprise — ne se
        # réessaie pas : il ne rendra pas mieux la cinquième fois.
        try:
            return frame(rendre_les_instructions(agent.spec.instructions, **variables))
        except TemplateError as erreur:
            qui = f"{agent.agent.slug}@{agent.version}"
            raise ApplicationError(
                f"the instructions of the agent `{qui}` do not render: {erreur}",
                type="InstructionsInvalides",
                non_retryable=True,
            ) from erreur
    return render_playbook(plan.playbook or plan.role, **variables)


def _agent_d_essai(plan: StagePlan) -> Any:
    """The draft version a trial runs (S32-03): it stands in for a registry agent — its
    instructions, model, backend, limits and skills — without being published."""
    if plan.trial_spec is None:
        return None
    from types import SimpleNamespace

    from choregos_api.schemas import AgentSpec

    return SimpleNamespace(
        agent=SimpleNamespace(slug="trial"), version=0, spec=AgentSpec.model_validate(plan.trial_spec)
    )


async def _agent_du_plan(session: Any, bundle: Any, plan: StagePlan) -> Any:
    """L'agent que l'acteur nomme, résolu pour ce projet ; `None` si l'acteur n'en nomme pas."""
    if not plan.agent:
        return None
    from choregos_api.services.agents import AgentIndisponible, resoudre_l_agent
    from temporalio.exceptions import ApplicationError

    try:
        # The work item decides a canary share, so every stage and retry of it gets one revision.
        resolved = await resoudre_l_agent(
            session, bundle.project.org_id, bundle.project.id, plan.agent, work_item_id=plan.work_item_id
        )
    except AgentIndisponible as refus:
        raise ApplicationError(str(refus), type="AgentIndisponible", non_retryable=True) from refus
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


async def _budget_du_jour(session: Any, bundle: Any, agent: Any) -> None:
    """Un agent qui a dépensé son budget du jour — modèles et outils — ne lance plus de run avant
    minuit (UTC). Le refus ne se réessaie pas : retenter ne rendrait pas le budget."""
    from choregos_api.services.agents import depense_du_jour, depense_du_mois
    from choregos_api.services.budget_alerts import suspend_for_budget
    from temporalio.exceptions import ApplicationError

    slug = agent.agent.slug
    org_id = bundle.project.org_id
    plafond = agent.spec.budget.daily_usd
    if plafond is not None:
        depense = await depense_du_jour(session, org_id, slug)
        if depense >= plafond:
            raise ApplicationError(
                f"run refused before it started — the agent `{slug}` spent {depense:.2f} $ "
                f"today, for a daily budget of {plafond:.2f} $",
                type="admission_refused",
                non_retryable=True,
            )
    monthly = agent.spec.budget.monthly_usd
    if monthly is not None:
        spent = await depense_du_mois(session, org_id, slug)
        if spent >= monthly:
            # The hard stop of the monthly cap (S27-09); normally the spend that crossed it has
            # already suspended the agent, this covers a cap set below what was already spent.
            await suspend_for_budget(session, org_id, slug, spent=spent, cap=monthly)
            raise ApplicationError(
                f"run refused before it started — the agent `{slug}` spent {spent:.2f} $ "
                f"this month, for a monthly budget of {monthly:.2f} $",
                type="admission_refused",
                non_retryable=True,
            )


async def _skills_de_l_agent(session: Any, bundle: Any, agent: Any) -> list[SkillRef]:
    """Les skills que la version de l'agent porte : leur version (dite, sinon la dernière) et son
    empreinte, que le runner vérifiera. Une skill absente de la bibliothèque arrête le run."""
    if agent is None or not agent.spec.skills:
        return []
    from choregos_api.db.models import Skill, SkillVersion
    from temporalio.exceptions import ApplicationError

    refs: list[SkillRef] = []
    for demandee in agent.spec.skills:
        requete = (
            select(SkillVersion)
            .join(Skill, Skill.id == SkillVersion.skill_id)
            .where(Skill.org_id == bundle.project.org_id, Skill.slug == demandee.slug)
        )
        if demandee.version is not None:
            requete = requete.where(SkillVersion.version == demandee.version)
        else:
            # Unpinned: the latest APPROVED version — never one waiting for its second reviewer
            # (S32-06, ADR 0050). An activity, not workflow code: no replay concern.
            requete = requete.where(SkillVersion.review_status == "approved")
        ligne = (
            await session.execute(requete.order_by(SkillVersion.version.desc()).limit(1))
        ).scalar_one_or_none()
        if ligne is not None and ligne.review_status != "approved":
            raise ApplicationError(
                f"the agent `{agent.agent.slug}` pins the skill `{demandee.slug}` v{ligne.version}, "
                "which is high risk and waits for a second reviewer",
                type="SkillPendingReview",
                non_retryable=True,
            )
        if ligne is None and demandee.version is None and await _skill_exists(session, bundle, demandee.slug):
            raise ApplicationError(
                f"the agent `{agent.agent.slug}` carries the skill `{demandee.slug}`, "
                "which has no approved version yet",
                type="SkillPendingReview",
                non_retryable=True,
            )
        if ligne is None:
            raise ApplicationError(
                f"the agent `{agent.agent.slug}` carries the skill `{demandee.slug}`, "
                "which is not in the library",
                type="SkillAbsente",
                non_retryable=True,
            )
        refs.append(SkillRef(slug=demandee.slug, version=ligne.version, digest=ligne.digest))
    return refs


async def _knowledge_of_agent(session: Any, bundle: Any, agent: Any, item: Any) -> list[KnowledgeRef]:
    """The knowledge bases the agent version reads (ADR 0051), pinned for this run. A base that does
    not exist, is not served to this project, or is restricted and not readable by the person who
    started the work item (ADR 0097), stops the run: it would answer without its reference."""
    if agent is None or not agent.spec.knowledge:
        return []
    from choregos_api.knowledge.on_behalf import OnBehalf
    from choregos_api.knowledge.serving import KnowledgeRefusedError, pin
    from choregos_core.restricted import mark
    from temporalio.exceptions import ApplicationError

    on_behalf = OnBehalf(session, bundle.project.org_id, bundle.project.id, item.initiated_by)
    try:
        refs = await pin(
            session,
            bundle.project.org_id,
            bundle.project.slug,
            [ref.slug for ref in agent.spec.knowledge],
            on_behalf,
        )
    except KnowledgeRefusedError as refusal:
        raise ApplicationError(
            f"the agent `{agent.agent.slug}` reads a knowledge base it cannot be served: {refusal}",
            type="KnowledgeRefused",
            non_retryable=True,
        ) from refusal
    # What this run writes lands under the work item: it is now read only by the bases' readers (ADR 0114).
    mark(item, on_behalf.restricted_served)
    return refs


async def _skill_exists(session: Any, bundle: Any, slug: str) -> bool:
    from choregos_api.db.models import Skill

    trouvee = await session.execute(
        select(Skill.id).where(Skill.org_id == bundle.project.org_id, Skill.slug == slug)
    )
    return trouvee.first() is not None


def _plan_de_l_agent(plan: StagePlan, spec: Any) -> StagePlan:
    """Le plan, avec ce que la version de l'agent fixe : modèle, backend, et des limites qui ne font
    que resserrer celles de l'acteur."""
    from dataclasses import replace

    from temporalio.exceptions import ApplicationError

    def plus_strict(de_l_acteur: int | None, de_l_agent: int | None) -> int | None:
        valeurs = [v for v in (de_l_acteur, de_l_agent) if v is not None]
        return min(valeurs) if valeurs else None

    runtime = getattr(spec, "runtime", None)
    backend = spec.backend
    if runtime is not None and runtime.kind == "code":
        # A code agent runs as the `code` backend and no other (ADR 0063): an actor that names
        # `codex` for it contradicts the version, which a retry would not change.
        if plan.backend and plan.backend != CODE_BACKEND:
            raise ApplicationError(
                f"the actor asks for the backend `{plan.backend}`, but the agent is written in code "
                f"(runtime `code`): drop `backend:` from the actor",
                type="AgentIndisponible",
                non_retryable=True,
            )
        backend = CODE_BACKEND
    elif runtime is not None and runtime.kind == "a2a":
        # A remote agent runs as no local backend (ADR 0062); an actor naming one contradicts it.
        if plan.backend and plan.backend != A2A_BACKEND:
            raise ApplicationError(
                f"the actor asks for the backend `{plan.backend}`, but the agent is remote (runtime "
                f"`a2a`): drop `backend:` from the actor",
                type="AgentIndisponible",
                non_retryable=True,
            )
        backend = A2A_BACKEND
    elif runtime is not None and runtime.kind == "prompt":
        # A prompt agent is the runner's own tool loop (ADR 0080): no coding agent to choose.
        if plan.backend and plan.backend != PROMPT_BACKEND:
            raise ApplicationError(
                f"the actor asks for the backend `{plan.backend}`, but the agent is a prompt agent "
                f"(runtime `prompt`): drop `backend:` from the actor",
                type="AgentIndisponible",
                non_retryable=True,
            )
        backend = PROMPT_BACKEND
    elif runtime is not None and runtime.kind == "hosted":
        # A hosted agent is its own container (ADR 0080): no coding agent to choose either.
        if plan.backend and plan.backend != HOSTED_BACKEND:
            raise ApplicationError(
                f"the actor asks for the backend `{plan.backend}`, but the agent is a hosted agent "
                f"(runtime `hosted`): drop `backend:` from the actor",
                type="AgentIndisponible",
                non_retryable=True,
            )
        backend = HOSTED_BACKEND
    elif runtime is not None and runtime.kind in STEPLESS_RUNTIMES:
        # ADR 0080: a voice agent never runs as a step; a step that started one would run nothing.
        raise ApplicationError(
            f"the agent's runtime `{runtime.kind}` cannot run as a workflow step "
            "(a voice agent runs only as a session)",
            type="AgentIndisponible",
            non_retryable=True,
        )
    elif runtime is not None and runtime.backend:
        backend = runtime.backend

    return replace(
        plan,
        model_request=spec.model or plan.model_request,
        backend=plan.backend or backend,
        max_turns=plus_strict(plan.max_turns, spec.limits.max_turns),
        max_minutes=plus_strict(plan.max_minutes, spec.limits.max_minutes),
    )


CODE_BACKEND = "code"
PROMPT_BACKEND = "prompt"
HOSTED_BACKEND = "hosted"
#: Runtimes no step can run (ADR 0080): a voice agent runs only as a session.
STEPLESS_RUNTIMES = frozenset({"voice"})


def _fixed_backend(agent: Any) -> bool:
    """Whether the agent's runtime fixes its backend (`code`, `prompt`, `hosted`): no rotation swaps it."""
    runtime = getattr(getattr(agent, "spec", None), "runtime", None)
    return runtime is not None and runtime.kind in {CODE_BACKEND, PROMPT_BACKEND, HOSTED_BACKEND}


def _prompt_call(agent: Any) -> PromptCall | None:
    """The step a prompt agent carries (ADR 0080), from its version's runtime; `None` for any other."""
    runtime = getattr(getattr(agent, "spec", None), "runtime", None)
    if runtime is None or runtime.kind != "prompt":
        return None
    return PromptCall(
        max_output_tokens=runtime.max_output_tokens,
        tool_choice=runtime.tool_choice,
        response_format=runtime.response_format,
    )


def _hosted_call(
    agent: Any, builds: Mapping[str, Any] | None = None, *, session: bool = False
) -> HostedCall | None:
    """The step a hosted agent carries (ADR 0080), from its version's runtime; `None` for any other.

    Refused, never retried, when no pod could run it as published: an image without a digest, a zip
    whose build is not `ready`, or, for a workflow step, a version that serves only `invocations_ws`
    (a `session`'s pod relays that socket, S41-21)."""
    from temporalio.exceptions import ApplicationError

    runtime = getattr(getattr(agent, "spec", None), "runtime", None)
    if runtime is None or runtime.kind != "hosted":
        return None
    who = f"`{agent.agent.slug}` v{agent.version}"
    socket = "invocations_ws" in runtime.protocols
    protocol = next((p for p in runtime.protocols if p in ("responses", "invocations")), None)
    if protocol is None and session and socket:
        protocol = "responses"  # never sent: a session reaches it only on `/realtime`
    if protocol is None:
        raise ApplicationError(
            f"the hosted agent {who} serves only `invocations_ws`; a workflow step is sent over "
            "`responses` or `invocations`",
            type="hosted_protocol_unsupported",
            non_retryable=True,
        )
    return HostedCall(
        protocol=protocol,
        image=_hosted_image(runtime, who, builds),
        port=runtime.port,
        readiness_timeout_s=runtime.readiness_timeout_s,
        termination_grace_s=runtime.termination_grace_s,
        workspace=runtime.workspace,
        env=dict(runtime.env),
        agent_slug=agent.agent.slug,
        agent_version=agent.version,
        socket=socket and session,
    )


def _hosted_image(runtime: Any, who: str, builds: Mapping[str, Any] | None = None) -> str:
    """`ref@sha256:…`: the digest publication pinned, or the image a zip's build produced, as its
    `hosted_builds` row says (the version's own `build` when there is no row)."""
    from temporalio.exceptions import ApplicationError

    source = runtime.source
    if source.type == "zip":
        build = runtime.build
        row = (builds or {}).get(build.key) if build is not None else None
        status = (row or build).status if build is not None else "not started"
        image = (row or build).image if build is not None else None
        if status != "ready" or not image or "@sha256:" not in image:
            raise ApplicationError(
                f"the hosted agent {who} is built from a zip whose build is {status}: a step runs "
                "only an image a `ready` build produced",
                type="hosted_build_not_ready",
                non_retryable=True,
            )
        return str(image)
    if not source.digest:
        raise ApplicationError(
            f"the hosted agent {who} names `{source.ref}` without a digest: publication pins every "
            "hosted image, and a step never runs a tag",
            type="hosted_image_unpinned",
            non_retryable=True,
        )
    return f"{source.ref.split('@', 1)[0]}@{source.digest}"


def _code_runtime(agent: Any) -> Any:
    """The `code` runtime of the resolved agent version, or `None` for any other."""
    runtime = getattr(getattr(agent, "spec", None), "runtime", None)
    return runtime if runtime is not None and runtime.kind == "code" else None


async def _reviewer_backend(
    session: Any,
    bundle: Any,
    item: Any,
    wanted: str,
    resolver: ModelResolver,
    plan: StagePlan,
) -> str:
    """Un relecteur qui n'est pas l'implémenteur (politique `review.cross_backend`).

    La garantie est un mécanisme : si le backend prévu pour la revue est celui qui a écrit
    le code, on en prend un autre — parmi ceux que le projet autorise et qui acceptent le
    modèle demandé. Si aucun ne convient, on garde celui d'origine et on le dit : bloquer
    un ticket parce qu'un seul backend est installé serait pire que la revue dégradée, et
    `GET /projects/{id}/metrics/cross-backend` compte ces cas.
    """
    implementer = (
        await session.execute(
            select(Run.backend)
            .where(
                Run.work_item_id == item.id,
                Run.stage_role == str(StageRole.IMPLEMENT),
                Run.backend.is_not(None),
            )
            .order_by(Run.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if not implementer or implementer != wanted:
        return wanted
    for candidate in reviewer_candidates(implementer, list(bundle.config.agent.allowed_backends)):
        stored = await _backend_row(session, candidate)
        if not backend_enabled(candidate, stored.enabled if stored else None):
            continue  # S42-07c: a backend the platform disabled is not a reviewer
        try:
            resolver.resolve(
                plan.model_request, project=bundle.config, size=item.size, backend=candidate, role=plan.role
            )
        except ModelResolutionError:
            continue
        logger.info(
            "revue croisée",
            project=bundle.slug,
            work_item=item.tracker_key,
            implementer=implementer,
            reviewer=candidate,
        )
        return str(candidate)
    logger.warning(
        "revue croisée impossible",
        project=bundle.slug,
        work_item=item.tracker_key,
        implementer=implementer,
        raison="aucun autre backend n'accepte le modèle demandé",
    )
    return wanted
