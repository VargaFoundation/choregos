# SPDX-License-Identifier: Apache-2.0
"""La préparation d'une étape agent : modèle, clé, contexte, `StageInput`.

Chaque fonction est une activité Temporal ; toutes sont idempotentes par `run_id`. Le
lancement et l'attente vivent dans `execution`, le bilan dans `bilan`, la garde contre
l'injection dans `garde` — ce module les réexporte, l'interpréteur et les tests continuent
de les lire ici.
"""

from __future__ import annotations

import asyncio
from hashlib import sha256
from typing import Any

from choregos_api.db.models import Run
from choregos_api.logging import bind, get_logger
from choregos_api.services import persist_event
from choregos_api.services.recus import LUE, consigner, lu_par_le_prompt
from choregos_contracts import (
    AgentRef,
    Budget,
    Callbacks,
    ContextPack,
    EventType,
    KnowledgeRef,
    LaunchSpec,
    McpServerRef,
    Permissions,
    PlaybookRef,
    ProjectRef,
    RepoRef,
    SkillRef,
    StageInput,
    StageRole,
    ToolsRef,
    TransitionRef,
    WorkItemLinks,
    WorkItemRef,
)
from choregos_core import KNOWN_BACKEND_NAMES, ModelResolutionError, ModelResolver, utcnow
from choregos_core.redaction import REDACTED
from sqlalchemy import select
from temporalio import activity

from ..config import get_settings
from ..context_reduction import forced_context, resolve_context, stage_input_payload
from .base import db, load_work_item, project_bundle, without_secrets
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
from .job import prepare_job
from .plan import StagePlan
from .run_key import reserve_key

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
    # The memory server serves the PROJECT's memory: an agent whose scope is its own (or none) does
    # not get it — it would read and write past its scope (S32-02).
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
        if agent is not None:
            plan = _plan_de_l_agent(plan, agent.spec)
        resolver = ModelResolver(
            gateway_url=settings.gateway_url, gateway_models=await _gateway_catalogue(bundle)
        )
        backend = plan.backend or bundle.config.agent.default_backend
        if plan.role == str(StageRole.REVIEW) and engine.cross_backend_review():
            backend = await _reviewer_backend(session, bundle, item, backend, resolver, plan)
        resolved = _resolve_model(resolver, plan, bundle, item, backend)
        budget = engine.budget_for(plan.role, item.size, turns_factor=resolved.turns_factor)
        if plan.max_turns:
            budget = Budget(usd=budget.usd, max_turns=plan.max_turns, max_minutes=budget.max_minutes)
        if plan.max_minutes:
            budget = Budget(usd=budget.usd, max_turns=budget.max_turns, max_minutes=plan.max_minutes)
        if plan.trial_budget_usd is not None:
            # A trial's cap only tightens: the gateway key is minted at this amount (S32-03).
            budget = Budget(
                usd=min(budget.usd, plan.trial_budget_usd),
                max_turns=budget.max_turns,
                max_minutes=budget.max_minutes,
            )
        reservation = await _reservation(
            session, bundle, item, run_id, agent, budget.usd, trial=plan.trial_spec is not None
        )
        if reservation.below(settings.min_run_reservation_usd):
            return await _refuse_on_budget(session, bundle, item, run_id, plan, reservation)
        budget = Budget(usd=reservation.usd, max_turns=budget.max_turns, max_minutes=budget.max_minutes)

        await _admettre(bundle, item, run_id, plan, backend, budget.usd)

        # The key itself is minted by `start_run`, which hands it to the run's Secret: no row and
        # no Temporal payload ever holds it (#523). Its cap is reserved here, where the budget
        # is decided, so that another run cannot take it in between; `start_run` turns this row
        # into the key's. Replayable: the row is found again by its run (ADR 0008).
        await reserve_key(session, bundle, run_id, budget)

        memory_scope = _memory_scope(agent)
        context_pack = await _context_pack(bundle, item, plan, memory_scope, agent)
        await garde_contre_l_injection(session, bundle, item, run_id, plan, context_pack)
        playbook_prompt = _render_playbook(plan, bundle, item, context_pack, agent)
        digest = sha256(playbook_prompt.encode()).hexdigest()[:12]
        source = (
            f"agent:{agent.agent.slug}@{agent.version}" if agent is not None else (plan.playbook or plan.role)
        )
        playbook_ref = f"{source}@sha256:{digest}"
        allowed_paths = list(item.allowed_paths or []) or ["**"]
        documents = item.documents or {}
        depot = bundle.config.repo
        stage_input = StageInput(
            run_id=run_id,
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
            if depot
            else None,
            agent=AgentRef(backend=backend, launch=LaunchSpec()),
            model=resolved.to_ref(),
            # In the run's Secret, never here (#523): see `start_run`.
            gateway_key=None,
            budget=budget,
            allowed_paths=allowed_paths,
            context_pack_url=f"{settings.object_store_url}/runs/{run_id}/context.json",
            playbook=PlaybookRef(ref=playbook_ref, prompt=playbook_prompt),
            tools=ToolsRef(mcp=_serveurs_mcp(settings, memory_scope)),
            memory_scope=memory_scope,
            skills=await _skills_de_l_agent(session, bundle, agent),
            knowledge=await _knowledge_of_agent(session, bundle, agent),
            permissions=Permissions(
                write_paths=allowed_paths,
                deny_commands=engine.deny_commands(),
                allow_domains=engine.allow_domains(),
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
        run.model = resolved.litellm_model
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
        # Ce que le prompt de ce run embarque des étapes d'avant, sous l'empreinte de la révision
        # embarquée (S25-04). Rejouable : un reçu existant ne se réécrit pas (ADR 0008).
        await consigner(
            session,
            org_id=bundle.project.org_id,
            work_item_id=item.id,
            run_id=run_id,
            stage=plan.role,
            sorties=lu_par_le_prompt(item.documents or {}, list(plan.inputs or []), playbook_prompt),
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
            model=resolved.litellm_model,
        )
        return {
            "run_id": run_id,
            "stage_input": without_secrets(payload, keep_gateway_key=False),
            "reused": False,
        }


def _resolve_model(resolver: ModelResolver, plan: StagePlan, bundle: Any, item: Any, backend: str) -> Any:
    """The stage's model, or a refusal that is not retried: a model the resolver refuses — unknown
    profile, no tool calling, a backend that does not take it — stays refused until an operator
    changes the configuration. The interpreter escalates the work item on it (#543)."""
    from temporalio.exceptions import ApplicationError

    try:
        return resolver.resolve(plan.model_request, project=bundle.config, size=item.size, backend=backend)
    except ModelResolutionError as refusal:
        raise ApplicationError(str(refusal), type="ModelResolutionError", non_retryable=True) from refusal


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
    agent's run, day and month budgets, the ticket budget and the project's day budget. A trial has
    no day or month budget: its runs belong to no agent, so no spend is counted against one (S32-03)."""
    from choregos_api.services.agents import depense_du_jour, depense_du_mois
    from choregos_api.services.budgets import (
        open_reservations,
        project_spent_since,
        reserve,
        start_of_day,
        ticket_budget,
        ticket_spent,
    )

    engine = bundle.engine
    left: dict[str, float | None] = {"stage": stage_usd}
    if agent is not None:
        left["agent_run"] = agent.spec.budget.run_usd
        if agent.spec.budget.daily_usd is not None and not trial:
            spent = await depense_du_jour(session, bundle.project.org_id, agent.agent.slug)
            left["agent_day"] = agent.spec.budget.daily_usd - spent
        if agent.spec.budget.monthly_usd is not None and not trial:
            # The monthly cap is a reservation too (S27-09): a run cannot spend past it.
            month = await depense_du_mois(session, bundle.project.org_id, agent.agent.slug)
            left["agent_month"] = agent.spec.budget.monthly_usd - month
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


def _memory_scope(agent: Any) -> str:
    """The agent version's memory scope (S32-02); `project` for a playbook, as before."""
    return str(getattr(getattr(agent, "spec", None), "memory", None) or "project")


async def _context_pack(
    bundle: Any, item: Any, plan: StagePlan, memory_scope: str = "project", agent: Any = None
) -> ContextPack:
    """Memory bounded by role; a failure yields an empty pack rather than blocking the stage. Read
    within the agent's scope (S32-02): the project's, its own, or none."""
    from choregos_core import memory_namespace

    engine = bundle.engine
    budget_tokens = engine.memory_budget(plan.role)
    espace = memory_namespace(bundle.slug, memory_scope, agent.agent.slug if agent is not None else None)
    if budget_tokens <= 0 or espace is None:
        return ContextPack.empty(item.title)
    kinds_by_role = {
        "refine": ["decision", "incident", "ticket_summary"],
        "plan": ["decision", "convention"],
        "implement": ["convention", "run_lesson", "hotspot"],
        "review": ["incident", "hotspot", "decision"],
        "verify": ["flaky_test", "run_lesson"],
    }
    query = f"{item.title}\n{(item.documents or {}).get('spec_markdown', '')}"[:2000]
    try:
        return await asyncio.wait_for(
            bundle.adapters.memory.context_pack(
                espace,
                query,
                list(item.allowed_paths or []),
                budget_tokens,
                kinds_by_role.get(plan.role),
            ),
            timeout=bundle.policy.memory.read_timeout_ms / 1000 * 10,
        )
    except (TimeoutError, Exception):
        return ContextPack.empty(query)


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
        "ticket": {"key": item.tracker_key, "title": item.title, "body": item.body_snapshot or ""},
        "spec": documents.get("spec_markdown", ""),
        "plan_markdown": documents.get("plan_markdown", ""),
        # Les entrées que la transition déclare (`inputs: [profils]`), lues sous leur nom :
        # c'est ainsi qu'une étape métier reçoit ce que la précédente a produit.
        "inputs": {nom: documents.get(nom, "") for nom in (plan.inputs or [])},
        "allowed_paths": list(item.allowed_paths or []),
        "context": context,
        "project": bundle.config,
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
        return await resoudre_l_agent(session, bundle.project.org_id, bundle.project.id, plan.agent)
    except AgentIndisponible as refus:
        raise ApplicationError(str(refus), type="AgentIndisponible", non_retryable=True) from refus


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


async def _knowledge_of_agent(session: Any, bundle: Any, agent: Any) -> list[KnowledgeRef]:
    """The knowledge bases the agent version reads (ADR 0051), pinned for this run. A base that does
    not exist, or is not served to this project, stops the run: it would answer without its reference."""
    if agent is None or not agent.spec.knowledge:
        return []
    from choregos_api.knowledge.serving import KnowledgeRefusedError, pin
    from temporalio.exceptions import ApplicationError

    try:
        return await pin(
            session,
            bundle.project.org_id,
            bundle.project.slug,
            [ref.slug for ref in agent.spec.knowledge],
        )
    except KnowledgeRefusedError as refusal:
        raise ApplicationError(
            f"the agent `{agent.agent.slug}` reads a knowledge base it cannot be served: {refusal}",
            type="KnowledgeRefused",
            non_retryable=True,
        ) from refusal


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

    def plus_strict(de_l_acteur: int | None, de_l_agent: int | None) -> int | None:
        valeurs = [v for v in (de_l_acteur, de_l_agent) if v is not None]
        return min(valeurs) if valeurs else None

    return replace(
        plan,
        model_request=spec.model or plan.model_request,
        backend=plan.backend or spec.backend,
        max_turns=plus_strict(plan.max_turns, spec.limits.max_turns),
        max_minutes=plus_strict(plan.max_minutes, spec.limits.max_minutes),
    )


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
    allowed = list(bundle.config.agent.allowed_backends) or list(KNOWN_BACKEND_NAMES)
    for candidate in allowed:
        if candidate == implementer:
            continue
        try:
            resolver.resolve(plan.model_request, project=bundle.config, size=item.size, backend=candidate)
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
