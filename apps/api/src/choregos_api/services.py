"""Services partagés par les routeurs : requêtes, conversions, effets de bord.

Les routeurs restent minces ; toute la logique réutilisable (par le CLI, l'orchestrateur
ou les webhooks) vit ici.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from choregos_contracts import ChoregosEvent, EventType, Policy, ProjectConfig, Workflow
from choregos_core import (
    PolicyEngine,
    checksum,
    load_preset,
    load_template,
    parse_policy,
    parse_workflow,
    template_yaml,
    utcnow,
)
from choregos_core.dsl import dump_workflow
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .db.models import (
    CostLedger,
    Event,
    HumanRequest,
    Organization,
    PolicyDef,
    Project,
    Release,
    Run,
    WorkflowDef,
    WorkItem,
)
from .events import diffuser
from .schemas import (
    CostEstimate,
    HumanRequestDto,
    ProjectDto,
    ProjectStats,
    ReleaseDto,
    RunDto,
    RunSummary,
    Totals,
    WorkflowFailure,
    WorkItemDto,
)

DEFAULT_WORKFLOW = "default-simple"
DEFAULT_POLICY_PRESET = "solo"


# ───────────────────────────── workflow & policy ─────────────────────────────


async def active_workflow(session: AsyncSession, project_id: str) -> WorkflowDef | None:
    return (
        await session.execute(
            select(WorkflowDef)
            .where(WorkflowDef.project_id == project_id, WorkflowDef.is_active.is_(True))
            .order_by(WorkflowDef.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def active_policy(session: AsyncSession, project_id: str) -> PolicyDef | None:
    return (
        await session.execute(
            select(PolicyDef)
            .where(PolicyDef.project_id == project_id, PolicyDef.is_active.is_(True))
            .order_by(PolicyDef.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def ensure_defaults(session: AsyncSession, project: Project) -> tuple[WorkflowDef, PolicyDef]:
    """Un projet neuf reçoit `default-simple` et le preset `solo` (D14)."""
    workflow = await active_workflow(session, project.id)
    if workflow is None:
        source = template_yaml(DEFAULT_WORKFLOW)
        parsed, _ = parse_workflow(source)
        workflow = WorkflowDef(
            project_id=project.id,
            name=parsed.metadata.name,
            version=parsed.metadata.version,
            source="template",
            yaml=source,
            json_doc=parsed.model_dump(mode="json", by_alias=True, exclude_none=True),
            checksum=checksum(parsed),
            is_active=True,
        )
        session.add(workflow)
    policy = await active_policy(session, project.id)
    if policy is None:
        from choregos_core import preset_yaml

        source = preset_yaml(DEFAULT_POLICY_PRESET)
        parsed_policy = parse_policy(source)
        policy = PolicyDef(
            project_id=project.id,
            name=parsed_policy.metadata.name,
            version=parsed_policy.metadata.version,
            yaml=source,
            json_doc=parsed_policy.model_dump(mode="json", exclude_none=True),
            is_active=True,
        )
        session.add(policy)
    await session.flush()
    return workflow, policy


def workflow_model(row: WorkflowDef | None) -> Workflow:
    if row is None:
        return load_template(DEFAULT_WORKFLOW)
    if row.json_doc:
        return Workflow.model_validate(row.json_doc)
    parsed, _ = parse_workflow(row.yaml)
    return parsed


def policy_model(row: PolicyDef | None) -> Policy:
    if row is None:
        return load_preset(DEFAULT_POLICY_PRESET)
    if row.json_doc:
        return Policy.model_validate(row.json_doc)
    return parse_policy(row.yaml)


async def policy_engine(session: AsyncSession, project_id: str) -> PolicyEngine:
    return PolicyEngine(policy_model(await active_policy(session, project_id)))


def project_config(project: Project) -> ProjectConfig:
    return ProjectConfig.model_validate(project.config)


def workflow_yaml(workflow: Workflow) -> str:
    return dump_workflow(workflow)


# ───────────────────────────── projections ─────────────────────────────


async def project_dto(session: AsyncSession, project: Project, org_slug: str | None = None) -> ProjectDto:
    if org_slug is None:
        org = await session.get(Organization, project.org_id)
        org_slug = org.slug if org else ""
    workflow = await active_workflow(session, project.id)
    policy = await active_policy(session, project.id)
    return ProjectDto(
        id=project.id,
        slug=project.slug,
        org=org_slug,
        name=project.name,
        template_ref=project.template_ref,
        status=project.status,
        config=project.config,
        workflow_name=workflow.name if workflow else None,
        policy_name=policy.name if policy else None,
        stats=await project_stats(session, project),
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


async def project_stats(session: AsyncSession, project: Project) -> ProjectStats:
    since = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    active = (
        await session.execute(
            select(func.count())
            .select_from(WorkItem)
            .where(WorkItem.project_id == project.id, WorkItem.closed_at.is_(None))
        )
    ).scalar_one()
    cost = (
        await session.execute(
            select(func.coalesce(func.sum(CostLedger.cost_eur), 0.0)).where(
                CostLedger.project_id == project.id, CostLedger.ts >= since
            )
        )
    ).scalar_one()
    trains = (
        await session.execute(
            select(func.count())
            .select_from(Release)
            .where(
                Release.project_id == project.id,
                Release.status.in_(["collecting", "departing", "staging", "awaiting_approval", "promoting"]),
            )
        )
    ).scalar_one()
    return ProjectStats(
        active_work_items=int(active),
        cost_month_eur=round(float(cost), 4),
        trains_pending=int(trains),
        first_pass_merge_rate=await first_pass_merge_rate(session, project.id),
    )


async def first_pass_merge_rate(session: AsyncSession, project_id: str) -> float | None:
    """Part des tickets fermés dont l'implémentation n'a demandé qu'une tentative."""
    items = (
        (
            await session.execute(
                select(WorkItem.id).where(WorkItem.project_id == project_id, WorkItem.closed_at.is_not(None))
            )
        )
        .scalars()
        .all()
    )
    if not items:
        return None
    good = 0
    for item_id in items:
        attempts = (
            await session.execute(
                select(func.max(Run.attempt)).where(
                    Run.work_item_id == item_id, Run.stage_role == "implement"
                )
            )
        ).scalar_one()
        if attempts is None or attempts <= 1:
            good += 1
    return round(good / len(items), 4)


def totals_from(raw: dict[str, Any] | None) -> Totals:
    return Totals.model_validate(raw or {})


def run_summary(run: Run) -> RunSummary:
    return RunSummary(
        id=run.id,
        status=run.status,
        stage_role=run.stage_role,
        attempt=run.attempt,
        backend=run.backend,
        model=run.model,
        cost_usd=run.cost_usd,
        started_at=run.started_at,
    )


def run_dto(run: Run, project_slug: str) -> RunDto:
    from choregos_contracts import StageResult

    return RunDto(
        id=run.id,
        status=run.status,
        stage_role=run.stage_role,
        attempt=run.attempt,
        backend=run.backend,
        model=run.model,
        cost_usd=run.cost_usd,
        started_at=run.started_at,
        work_item_id=run.work_item_id,
        project_slug=project_slug,
        transition_id=run.transition_id,
        actor=run.actor,
        executor_kind=run.executor_kind,
        executor_ref=run.executor_ref,
        ended_at=run.ended_at,
        tokens=totals_from(run.tokens),
        gateway_key_id=run.gateway_key_id,
        result=StageResult.model_validate(run.result) if run.result else None,
        transcript_url=run.transcript_url,
        context_pack_url=run.context_pack_url,
        playbook_checksum=run.playbook_checksum,
        allowed_paths=list(run.allowed_paths or []),
    )


def human_request_dto(row: HumanRequest) -> HumanRequestDto:
    return HumanRequestDto(
        id=row.id,
        work_item_id=row.work_item_id,
        transition_id=row.transition_id,
        kind=row.kind,
        payload=row.payload,
        requested_at=row.requested_at,
        due_at=row.due_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision=row.decision,
    )


async def work_item_dto(
    session: AsyncSession, item: WorkItem, project: Project, *, with_temporal: bool = False
) -> WorkItemDto:
    workflow_row = await session.get(WorkflowDef, item.workflow_def_id) if item.workflow_def_id else None
    workflow = workflow_model(workflow_row)
    state = workflow.states.get(item.state)
    current = (
        await session.execute(
            select(Run)
            .where(Run.work_item_id == item.id, Run.status.in_(["queued", "running"]))
            .order_by(Run.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    pending = (
        await session.execute(
            select(HumanRequest)
            .where(HumanRequest.work_item_id == item.id, HumanRequest.decided_at.is_(None))
            .order_by(HumanRequest.requested_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return WorkItemDto(
        id=item.id,
        project_slug=project.slug,
        tracker_key=item.tracker_key,
        title=item.title,
        body_snapshot=item.body_snapshot,
        url=item.url,
        size=item.size,
        risk=item.risk,
        state=item.state,
        state_display=state.display if state else item.state,
        workflow_name=workflow_row.name if workflow_row else workflow.metadata.name,
        workflow_version=workflow_row.version if workflow_row else workflow.metadata.version,
        temporal_wf_id=item.temporal_wf_id,
        workflow_status=await _statut_temporal(item) if with_temporal else None,
        failure=WorkflowFailure(**item.failure) if item.failure else None,
        paused=item.paused,
        current_run=run_summary(current) if current else None,
        pending_request=human_request_dto(pending) if pending else None,
        pr_url=item.pr_url,
        totals=totals_from(item.totals),
        estimate=await estimate_cost(session, project.id, item),
        created_at=item.created_at,
        closed_at=item.closed_at,
    )


async def _statut_temporal(item: WorkItem) -> str | None:
    """Le statut du workflow du ticket, si Temporal le connaît.

    Jamais une erreur : un ticket doit se lire sans Temporal.
    """
    if not item.temporal_wf_id:
        return None
    from .temporal import get_temporal

    try:
        state = await get_temporal().describe(item.temporal_wf_id)
    except Exception:
        return None
    return state.status if state else None


def release_dto(row: Release, project_slug: str) -> ReleaseDto:
    return ReleaseDto(
        id=row.id,
        project_slug=project_slug,
        env=row.env,
        batch_no=row.batch_no,
        status=row.status,
        items=row.items,
        started_at=row.started_at,
        ended_at=row.ended_at,
        approved_by=row.approved_by,
        verdict=row.verdict,
        notes=row.notes,
        promotion_url=row.promotion_url,
    )


# ───────────────────────────── coûts ─────────────────────────────


async def estimate_cost(session: AsyncSession, project_id: str, item: WorkItem) -> CostEstimate | None:
    """Médiane et p80 des tickets comparables sur 90 jours (S4-04)."""
    if not item.size:
        return None
    since = datetime.now(UTC) - timedelta(days=90)
    rows = (
        (
            await session.execute(
                select(WorkItem.totals).where(
                    WorkItem.project_id == project_id,
                    WorkItem.size == item.size,
                    WorkItem.closed_at.is_not(None),
                    WorkItem.closed_at >= since,
                )
            )
        )
        .scalars()
        .all()
    )
    costs = sorted(float((t or {}).get("cost_usd", 0.0)) for t in rows if t)
    costs = [c for c in costs if c > 0]
    if not costs:
        return None
    median = costs[len(costs) // 2]
    p80 = costs[min(len(costs) - 1, int(len(costs) * 0.8))]
    spent = float((item.totals or {}).get("cost_usd", 0.0))
    return CostEstimate(median_usd=median, p80_usd=p80, sample_size=len(costs), over_p80=spent > p80)


async def record_cost(
    session: AsyncSession,
    *,
    project_id: str,
    work_item_id: str | None,
    run_id: str | None,
    provider: str,
    model: str,
    backend: str | None,
    stage_role: str | None,
    size: str | None,
    tokens_in: int,
    tokens_out: int,
    tokens_cached: int,
    cost_usd: float,
    fx_rate: float,
) -> CostLedger:
    entry = CostLedger(
        project_id=project_id,
        work_item_id=work_item_id,
        run_id=run_id,
        provider=provider,
        model=model,
        backend=backend,
        stage_role=stage_role,
        size=size,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tokens_cached=tokens_cached,
        cost_usd=cost_usd,
        cost_eur=round(cost_usd * fx_rate, 6),
        fx_rate=fx_rate,
        ts=utcnow(),
    )
    session.add(entry)
    await diffuser(
        session,
        ChoregosEvent.emit(
            EventType.COST_RECORDED,
            source="/choregos/api",
            subject=run_id,
            cost_usd=cost_usd,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        ),
    )
    return entry


# ───────────────────────────── événements ─────────────────────────────


DOCUMENTS_LOGICIELS = ("spec_markdown", "plan_markdown", "review_markdown", "release_notes_markdown")


def ranger_les_sorties(item: WorkItem, outputs: Any, declarees: list[str] | None = None) -> dict[str, Any]:
    """Range les sorties d'une étape dans `item.documents`, pour que l'étape SUIVANTE les lise.

    Les quatre documents logiciels étaient rangés ; les sorties nommées par le métier
    (`profils`, `evaluation`) ne l'étaient jamais — `outputs_present` les voyait dans le
    résultat, puis elles disparaissaient. Sur le banc du 2026-09-24, la qualification RH
    cherchait « les profils proposés à l'étape précédente » dans un workspace vide.
    Une sortie déclarée par la transition (`outputs: [profils]`) est rangée sous son nom.
    """
    documents = dict(item.documents or {})
    valeurs = (
        outputs.model_dump(mode="json", exclude_none=True)
        if hasattr(outputs, "model_dump")
        else dict(outputs)
    )
    for champ in DOCUMENTS_LOGICIELS:
        if valeurs.get(champ):
            documents[champ] = valeurs[champ]
    for nom in declarees or []:
        if nom in valeurs and valeurs[nom] not in (None, "", [], {}):
            documents[nom] = valeurs[nom]
    item.documents = documents
    return documents


async def persist_event(
    session: AsyncSession,
    type_: EventType,
    *,
    project_id: str | None = None,
    work_item_id: str | None = None,
    subject: str | None = None,
    project_slug: str | None = None,
    **payload: Any,
) -> Event:
    """Persiste dans `events` **et** publie sur le bus SSE : un seul chemin pour tout."""
    row = Event(
        project_id=project_id,
        work_item_id=work_item_id,
        type=str(type_),
        subject=subject,
        payload=payload,
        ts=utcnow(),
    )
    session.add(row)
    await diffuser(
        session,
        ChoregosEvent.emit(
            type_, source="/choregos/api", subject=subject, project_slug=project_slug, **payload
        ),
    )
    return row


# ───────────────────── preuve avant dépendance : A/B de la mémoire ─────────────────────

MIN_TICKETS_PAR_GROUPE = 10
"""En dessous, on ne conclut pas : dix tickets fermés par bras, au minimum."""


async def memory_ab_comparison(session: AsyncSession, org: str, weeks: int = 4) -> dict[str, Any]:
    """Compare les projets **avec** et **sans** context pack (docs/plan/04, décision 4).

    La mémoire n'est pas un acquis : elle doit payer. On mesure, sur la fenêtre demandée,
    le taux de PR mergée au premier passage et le coût par ticket, groupe contre groupe.
    """
    from statistics import median

    since = utcnow() - timedelta(weeks=weeks)
    buckets: dict[str, dict[str, Any]] = {
        "with_memory": {"projects": [], "rates": [], "costs": [], "tickets": 0},
        "without_memory": {"projects": [], "rates": [], "costs": [], "tickets": 0},
    }
    organization = (
        await session.execute(select(Organization).where(Organization.slug == org))
    ).scalar_one_or_none()
    if organization is not None:
        # Tout projet non archivé compte : ce qui décide de l'échantillon, ce sont les
        # tickets fermés sur la fenêtre, pas le statut administratif du projet.
        projects = (
            (
                await session.execute(
                    select(Project).where(Project.org_id == organization.id, Project.status != "archived")
                )
            )
            .scalars()
            .all()
        )
        for project in projects:
            policy = policy_model(await active_policy(session, project.id))
            bucket = buckets["with_memory" if policy.memory.enabled else "without_memory"]
            stats = await _window_stats(session, project.id, since)
            if stats["tickets"] == 0:
                continue
            bucket["projects"].append(project.slug)
            bucket["tickets"] += stats["tickets"]
            if stats["first_pass_rate"] is not None:
                bucket["rates"].append(stats["first_pass_rate"])
            bucket["costs"].extend(stats["costs"])

    groups = {
        name: {
            "projects": sorted(bucket["projects"]),
            "tickets": bucket["tickets"],
            "first_pass_merge_rate": round(median(bucket["rates"]), 4) if bucket["rates"] else None,
            "cost_per_ticket_usd": round(median(bucket["costs"]), 4) if bucket["costs"] else None,
        }
        for name, bucket in buckets.items()
    }
    verdict, detail = _ab_verdict(groups)
    return {
        "org": org,
        "weeks": weeks,
        "since": since.isoformat(),
        "groups": groups,
        "verdict": "organisation inconnue" if organization is None else verdict,
        "detail": detail,
    }


async def _window_stats(session: AsyncSession, project_id: str, since: datetime) -> dict[str, Any]:
    """Tickets fermés sur la fenêtre : premier passage et coût, ticket par ticket."""
    items = (
        (
            await session.execute(
                select(WorkItem).where(
                    WorkItem.project_id == project_id,
                    WorkItem.closed_at.is_not(None),
                    WorkItem.closed_at >= since,
                )
            )
        )
        .scalars()
        .all()
    )
    if not items:
        return {"tickets": 0, "first_pass_rate": None, "costs": []}
    first_pass = 0
    costs: list[float] = []
    for item in items:
        runs = (await session.execute(select(Run).where(Run.work_item_id == item.id))).scalars().all()
        attempts = [run.attempt for run in runs if run.stage_role == "implement"]
        if not attempts or max(attempts) <= 1:
            first_pass += 1
        costs.append(round(sum(run.cost_usd or 0.0 for run in runs), 6))
    return {"tickets": len(items), "first_pass_rate": round(first_pass / len(items), 4), "costs": costs}


def _ab_verdict(groups: dict[str, Any]) -> tuple[str, str]:
    """Le verdict est explicite, y compris quand il n'y en a pas."""
    avec, sans = groups["with_memory"], groups["without_memory"]
    if avec["tickets"] < MIN_TICKETS_PAR_GROUPE or sans["tickets"] < MIN_TICKETS_PAR_GROUPE:
        return (
            "échantillon insuffisant",
            f"{avec['tickets']} ticket(s) fermé(s) avec mémoire, {sans['tickets']} sans : "
            f"il en faut {MIN_TICKETS_PAR_GROUPE} de chaque côté pour conclure.",
        )
    rate_avec, rate_sans = avec["first_pass_merge_rate"], sans["first_pass_merge_rate"]
    cost_avec, cost_sans = avec["cost_per_ticket_usd"], sans["cost_per_ticket_usd"]
    if rate_avec is None or rate_sans is None or cost_avec is None or cost_sans is None:
        return ("échantillon insuffisant", "un des deux groupes n'a ni taux ni coût mesurable.")
    delta_rate = rate_avec - rate_sans
    delta_cost = cost_avec - cost_sans
    detail = (
        f"premier passage : {rate_avec:.0%} avec mémoire contre {rate_sans:.0%} sans "
        f"({delta_rate:+.1%}) · coût par ticket : {cost_avec:.2f} $ contre {cost_sans:.2f} $ "
        f"({delta_cost:+.2f} $)."
    )
    if delta_rate > 0.05 and delta_cost <= 0:
        return ("la mémoire paie", detail)
    if delta_rate < -0.05 or delta_cost > 0.2 * max(cost_sans, 0.01):
        return ("la mémoire ne paie pas", detail)
    return ("pas de différence nette", detail)
