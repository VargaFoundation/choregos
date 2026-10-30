# SPDX-License-Identifier: Apache-2.0
"""Projections des tickets, runs, demandes humaines et releases ; la clé d'un ticket interne ;
le rangement des sorties d'une étape."""

from __future__ import annotations

from typing import Any

import httpx
import structlog
from choregos_adapters.errors import AdapterError
from choregos_contracts import EventType
from choregos_core.domain import NewItem
from choregos_core.restricted import RESTRICTED_TITLE, WITHHELD, mark
from choregos_core.secrets import SecretIntrouvable
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import (
    Connector,
    CostLedger,
    Event,
    Finding,
    HumanRequest,
    Project,
    Release,
    Run,
    WorkItem,
)
from ..errors import upstream
from ..rbac import Principal
from ..schemas import (
    HumanRequestDto,
    ReleaseDto,
    ReleaseItemDto,
    RunDto,
    RunSummary,
    TimelineEntry,
    ToolConnector,
    Totals,
    TrackerWrite,
    WorkflowFailure,
    WorkItemCreate,
    WorkItemDto,
)
from ..temporal import get_temporal, interpreter_id
from .couts import estimate_cost
from .definitions import workflow_du_ticket, workflow_model
from .evenements import persist_event
from .initiator import of_principal
from .routage import Naissance, nouveau_ticket, verifier_la_naissance


def _detail_de_la_decision(decision: dict[str, Any]) -> str:
    """Ce qu'une décision dit d'elle-même ; une tâche, sa preuve : la phrase attestée et les valeurs."""
    morceaux = []
    if decision.get("attestation"):
        morceaux.append(f"attested: “{decision['attestation']}”")
    valeurs = dict(decision.get("values") or {})
    if valeurs:
        morceaux.append(", ".join(f"{nom} = {valeur}" for nom, valeur in sorted(valeurs.items())))
    if decision.get("reason"):
        morceaux.append(str(decision["reason"]))
    return " — ".join(morceaux)


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


def run_dto(run: Run, project_slug: str, tool_connectors: list[ToolConnector] | None = None) -> RunDto:
    """A run as the API serves it; `tool_connectors` is filled by the routes that resolve them
    (`tool_connectors_of`), which a list of runs does not do for each one."""
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
        agent_slug=run.agent_slug,
        agent_version=run.agent_version,
        initiated_by=run.initiated_by,
        tool_connectors=list(tool_connectors or []),
    )


async def tool_connectors_of(session: AsyncSession, run: Run, project: Project) -> list[ToolConnector]:
    """The organisation connectors this run's agent may call through the broker, and the account each
    one calls as (ADR 0107): the broker's own selection, resolved here so that a project member who
    cannot read the organisation's connectors still sees them. Never a secret."""
    from choregos_adapters import account_of

    from .broker import broker_tools

    seen: dict[str, ToolConnector] = {}
    for tool in (await broker_tools(session, run, project)).values():
        connector = tool.connector
        if connector.name in seen:
            continue
        config = dict(connector.config or {})
        # An MCP connector sends its token as a Bearer unless it says `basic` (S39-10e).
        scheme = (config.get("auth_scheme") or "bearer") if connector.kind == "mcp" else None
        seen[connector.name] = ToolConnector(
            name=connector.name,
            type=connector.type,
            auth_scheme=str(scheme) if scheme else None,
            account=account_of(connector.kind, connector.type, config),
        )
    return sorted(seen.values(), key=lambda c: c.name)


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
    # Le workflow DU TICKET : sa version épinglée, sinon le défaut du projet — plus le gabarit
    # `default-simple`, qui faisait afficher un workflow que le projet n'avait jamais eu.
    workflow_row = await workflow_du_ticket(session, item)
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
    totals = totals_from(item.totals)
    # What the ticket has cost is the ledger's sum — models AND tools (S27-03): the counter kept on
    # the row adds model spend only. A ticket with no ledger line keeps its counter.
    booked = (
        await session.execute(
            select(func.sum(CostLedger.cost_usd), func.sum(CostLedger.cost_eur)).where(
                CostLedger.work_item_id == item.id
            )
        )
    ).one()
    if booked[0] is not None:
        totals = totals.model_copy(update={"cost_usd": float(booked[0]), "cost_eur": float(booked[1] or 0.0)})
    return WorkItemDto(
        id=item.id,
        fields=dict(item.fields or {}),
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
        totals=totals,
        budget_usd=item.budget_usd,
        estimate=await estimate_cost(session, project.id, item),
        initiated_by=item.initiated_by,
        restricted_knowledge=list(item.restricted_knowledge or []),
        created_at=item.created_at,
        closed_at=item.closed_at,
    )


async def _statut_temporal(item: WorkItem) -> str | None:
    """Le statut du workflow du ticket, si Temporal le connaît.

    Jamais une erreur : un ticket doit se lire sans Temporal.
    """
    if not item.temporal_wf_id:
        return None
    try:
        state = await get_temporal().describe(item.temporal_wf_id)
    except Exception:
        return None
    return state.status if state else None


def release_dto(row: Release, project_slug: str) -> ReleaseDto:
    # Le train range dans la release le signal d'embarquement tel quel (`approval`, `merged_at`,
    # `infra_pr_url`, `start_payload`…) ; le contrat n'en expose que les champs de l'élément et
    # refuse le reste. Sur le locataire dev, le 10/10, la première release réelle rendait 500 à
    # `getTrainStatus` et `listReleases` (S22-25).
    champs = ReleaseItemDto.model_fields
    return ReleaseDto(
        id=row.id,
        project_slug=project_slug,
        env=row.env,
        batch_no=row.batch_no,
        status=row.status,
        items=[{k: v for k, v in item.items() if k in champs} for item in row.items or []],
        started_at=row.started_at,
        ended_at=row.ended_at,
        approved_by=row.approved_by,
        verdict=row.verdict,
        notes=row.notes,
        promotion_url=row.promotion_url,
    )


async def cle_de_ticket_interne(session: AsyncSession, project: Project, *, numerotee: bool = True) -> str:
    """Attribue `<PRÉFIXE>-<n>` quand la plateforme tient elle-même les tickets (`tracker: internal`).

    Compter en base plutôt que tenir un compteur évite un deuxième endroit où l'état vit ;
    la course entre deux créations simultanées est laissée à la contrainte d'unicité
    `(project_id, tracker_key)`. Servait à l'orchestrateur (findings) ; sert aussi à l'API
    depuis que `POST /projects/{id}/work-items` existe.

    `numerotee=False` returns the prefix alone: a schedule builds its own key on it (S38-11).
    """
    connecteur = (
        await session.execute(
            select(Connector).where(Connector.project_id == project.id, Connector.kind == "tracker")
        )
    ).scalar_one_or_none()
    configure = (connecteur.config or {}).get("key_prefix") if connecteur else None
    prefixe = str(configure or project.slug).upper()
    if not numerotee:
        return prefixe
    existantes = (
        (
            await session.execute(
                select(WorkItem.tracker_key).where(
                    WorkItem.project_id == project.id, WorkItem.tracker_key.like(f"{prefixe}-%")
                )
            )
        )
        .scalars()
        .all()
    )
    numeros = [int(suffixe) for cle in existantes if (suffixe := cle.rsplit("-", 1)[-1]).isdigit()]
    return f"{prefixe}-{max(numeros, default=0) + 1}"


async def le_tracker_est_interne(session: AsyncSession, project: Project) -> bool:
    connecteur = (
        await session.execute(
            select(Connector).where(Connector.project_id == project.id, Connector.kind == "tracker")
        )
    ).scalar_one_or_none()
    return connecteur is None or connecteur.type in {"internal", "fake"}


async def tracker_du_projet(session: AsyncSession, project: Project) -> tuple[Connector | None, Any]:
    """The project's tracker connector and, when the tracker is external, its adapter.

    Returns `(row, None)` for an internal tracker (no row, `internal`, `fake`): the platform mints
    the keys itself. Kept apart so a test can replace it with a `FakeTracker`.
    """
    row = (
        await session.execute(
            select(Connector).where(Connector.project_id == project.id, Connector.kind == "tracker")
        )
    ).scalar_one_or_none()
    if row is None or row.type in {"internal", "fake"}:
        return row, None
    from choregos_adapters import build, configuration_resolue

    from .github_app import pin_installation

    try:
        from .plugin_tracker import pin_plugin_tracker

        pinned = await pin_installation(session, project.org_id, "tracker", row.type, row.config)
        pinned = await pin_plugin_tracker(session, project.org_id, "tracker", row.type, pinned)
        config = configuration_resolue("tracker", row.type, pinned, row.secret_refs, row.secret_ref)
        return row, build("tracker", row.type, config)
    except (AdapterError, SecretIntrouvable, ValueError, KeyError) as erreur:
        raise upstream(row.type, f"the tracker connector cannot be built: {erreur}") from erreur


ORIGIN_MARKER = "<!-- choregos:origin -->"
FILED_LABEL = "source:choregos"


DOCUMENTS_LOGICIELS = ("spec_markdown", "plan_markdown", "review_markdown", "release_notes_markdown")


def sorties_a_ranger(outputs: Any, declarees: list[str] | None = None) -> dict[str, Any]:
    """Les sorties d'une étape qui se rangent dans `item.documents` : les quatre documents logiciels
    présents, et chaque sortie que la transition déclare. Ce sont elles qui reçoivent un reçu (S25-04)."""
    valeurs = (
        outputs.model_dump(mode="json", exclude_none=True)
        if hasattr(outputs, "model_dump")
        else dict(outputs)
    )
    rangees = {champ: valeurs[champ] for champ in DOCUMENTS_LOGICIELS if valeurs.get(champ)}
    for nom in declarees or []:
        if nom in valeurs and valeurs[nom] not in (None, "", [], {}):
            rangees[nom] = valeurs[nom]
    return rangees


def ranger_les_sorties(item: WorkItem, outputs: Any, declarees: list[str] | None = None) -> dict[str, Any]:
    """Range les sorties d'une étape dans `item.documents`, pour que l'étape SUIVANTE les lise.

    Les quatre documents logiciels étaient rangés ; les sorties nommées par le métier
    (`profils`, `evaluation`) ne l'étaient jamais — `outputs_present` les voyait dans le
    résultat, puis elles disparaissaient. Sur le banc du 2026-09-24, la qualification RH
    cherchait « les profils proposés à l'étape précédente » dans un workspace vide.
    Une sortie déclarée par la transition (`outputs: [profils]`) est rangée sous son nom.
    """
    documents = {**(item.documents or {}), **sorties_a_ranger(outputs, declarees)}
    item.documents = documents
    return documents


async def creer_un_ticket(  # noqa: PLR0913 - one creation path, the birth facts keyword-only
    session: AsyncSession,
    principal: Principal,
    project: Project,
    demande: WorkItemCreate,
    *,
    canal: str = "api",
    cle: str | None = None,
    initiated_by: str | None = None,
    item_id: str | None = None,
    restricted_knowledge: list[str] | None = None,
) -> WorkItem:
    """Files a work item in Choregos, whatever the project's tracker (ADR 0059).

    The only creation path of a person's request: the REST route and the MCP door (ADR 0030) both
    use it, so a work item created from Claude differs in nothing from one created in the console.
    With an internal tracker the platform mints the key (`<PREFIX>-<n>`). With an external one
    (GitHub, Jira, GitLab) it first creates the issue there, labelled `source:choregos` and NOT
    `agent-ready` (the webhook must not race this transaction), and records the work item under
    the issue's key. The initial state is the chosen workflow's, and the interpreter starts at once
    unless `start=False`. `item_id` is a caller-derived id (ADR 0008), the A2A door's (ADR 0078).

    `initiated_by` names who started it when the principal does not (a schedule, a parent work
    item); otherwise it is the principal through `canal` (ADR 0077 §2).

    `restricted_knowledge` marks it at birth (ADR 0114): a fan-out child of a marked parent. An
    external tracker then receives a neutral title and no body; the work item keeps its own.
    """
    row, adapter = await tracker_du_projet(session, project)
    url: str | None = None
    if adapter is None:
        # A schedule gives its own key, one per window, so that a second firing finds the first (S38-11).
        key = cle or await cle_de_ticket_interne(session, project)
    else:
        # Validate first: a refused field or budget must not leave an issue behind.
        await verifier_la_naissance(
            session,
            project,
            Naissance(
                tracker_key="-",
                title=demande.title,
                size=demande.size,
                workflow=demande.workflow,
                labels=tuple(demande.labels),
                fields=dict(demande.fields),
                budget_usd=demande.budget_usd,
            ),
        )
        assert row is not None
        # A schedule gives its own key, one per window (S38-11); an external tracker mints its own.
        outward = (
            demande.model_copy(update={"title": RESTRICTED_TITLE, "body": WITHHELD})
            if restricted_knowledge
            else demande
        )
        key, url = await _classer_dans_le_tracker(row.type, adapter, principal, outward)
    item = await nouveau_ticket(
        session,
        project,
        Naissance(
            tracker_key=key,
            title=demande.title,
            body=demande.body,
            url=url,
            size=demande.size,
            risk=demande.risk,
            created_by=principal.email,
            initiated_by=initiated_by or of_principal(principal, canal),
            workflow=demande.workflow,
            labels=tuple(demande.labels),
            fields=dict(demande.fields),
            budget_usd=demande.budget_usd,
            item_id=item_id,
        ),
    )
    mark(item, restricted_knowledge or [])
    await persist_event(
        session,
        EventType.WORKITEM_CREATED,
        project_id=project.id,
        work_item_id=item.id,
        project_slug=project.slug,
        subject=key,
        key=key,
        title=demande.title,
    )
    if demande.start:
        await start_work_item(project, item)
    await record(
        session,
        principal,
        "workitem.create",
        org_id=project.org_id,
        target_type="work_item",
        target_id=item.id,
        key=key,
        canal=canal,
        tracker=row.type if row is not None else "internal",
    )
    return item


async def start_work_item(project: Project, item: WorkItem) -> None:
    """Starts the work item's interpreter: what `creer_un_ticket` does unless `start=False`.

    The A2A door starts it itself, once the files a message carried are stored: the first step
    must find the inputs it declares (ADR 0078 §6)."""
    workflow_id = interpreter_id(project.slug, item.tracker_key)
    await get_temporal().start_interpreter(
        workflow_id,
        {
            "project_id": project.id,
            "project_slug": project.slug,
            "work_item_id": item.id,
            "tracker_key": item.tracker_key,
        },
    )
    item.temporal_wf_id = workflow_id


async def _classer_dans_le_tracker(
    tracker_type: str, adapter: Any, principal: Principal, demande: WorkItemCreate
) -> tuple[str, str | None]:
    """Creates the issue in the external tracker; returns its key and, best effort, its URL."""
    body = f"{demande.body or ''}\n\n{ORIGIN_MARKER}Filed from Choregos by {principal.email}".strip()
    labels = [*dict.fromkeys([*demande.labels, FILED_LABEL])]
    try:
        key = str(await adapter.create_item(NewItem(title=demande.title, body=body, labels=labels)))
    except (AdapterError, SecretIntrouvable, httpx.HTTPError) as erreur:
        raise upstream(
            tracker_type,
            f"the tracker {tracker_type} refused the issue: {erreur}. Nothing was created in Choregos.",
        ) from erreur
    try:
        return key, (await adapter.fetch_item(key)).url
    except Exception as erreur:
        structlog.get_logger().warning("issue_url_unavailable", key=key, error=str(erreur))
        return key, None


def _via(value: Any) -> TrackerWrite | None:
    """A recorded tracker write (`{"type", "account"}`), or nothing for an event from before S42-06g."""
    if not isinstance(value, dict) or not value.get("type"):
        return None
    account = value.get("account")
    return TrackerWrite(type=str(value["type"]), account=str(account) if account else None)


async def chronologie(session: AsyncSession, item: WorkItem) -> list[TimelineEntry]:
    """États, runs, décisions, findings : l'histoire complète d'un ticket, dans l'ordre."""
    entries: list[TimelineEntry] = []
    runs = (
        (await session.execute(select(Run).where(Run.work_item_id == item.id).order_by(Run.created_at)))
        .scalars()
        .all()
    )
    for run in runs:
        summary = (run.result or {}).get("summary", "")
        entries.append(
            TimelineEntry(
                ts=run.started_at or run.created_at,
                kind="run",
                title=f"{run.stage_role} — tentative {run.attempt}",
                detail=summary or run.status,
                actor=run.actor or run.backend,
                actor_kind="agent",
                ref_id=run.id,
                cost_usd=run.cost_usd,
            )
        )
    requests = (
        (
            await session.execute(
                select(HumanRequest)
                .where(HumanRequest.work_item_id == item.id)
                .order_by(HumanRequest.requested_at)
            )
        )
        .scalars()
        .all()
    )
    events = (
        (await session.execute(select(Event).where(Event.work_item_id == item.id).order_by(Event.ts)))
        .scalars()
        .all()
    )
    # The tracker write behind a request (ADR 0107 point 4): its question went out as a comment.
    via_of_request = {
        str(e.payload.get("request_id")): e.payload.get("via")
        for e in events
        if e.type == str(EventType.WORKITEM_HUMAN_REQUESTED) and e.payload.get("via")
    }
    for req in requests:
        entries.append(
            TimelineEntry(
                ts=req.requested_at,
                kind="decision",
                title=f"human request: {req.kind}",
                detail=str(req.payload.get("question") or req.payload.get("summary") or ""),
                actor=req.decided_by,
                actor_kind="user",
                ref_id=req.id,
                via=_via(via_of_request.get(req.id)),
            )
        )
        if req.decided_at:
            entries.append(
                TimelineEntry(
                    ts=req.decided_at,
                    kind="decision",
                    title=f"decision: {(req.decision or {}).get('kind', 'answered')}",
                    detail=_detail_de_la_decision(req.decision or {}),
                    actor=req.decided_by,
                    actor_kind="user",
                    ref_id=req.id,
                )
            )
    for event in events:
        if event.type == str(EventType.WORKITEM_STATE_CHANGED):
            entries.append(
                TimelineEntry(
                    ts=event.ts,
                    kind="state_change",
                    title=f"{event.payload.get('from', '?')} → {event.payload.get('to', '?')}",
                    detail=str(event.payload.get("reason") or ""),
                    actor=str(event.payload.get("by") or "orchestrateur"),
                    actor_kind="system",
                    via=_via(event.payload.get("via")),
                )
            )
    findings = (
        (await session.execute(select(Finding).where(Finding.origin_work_item_id == item.id))).scalars().all()
    )
    for finding in findings:
        entries.append(
            TimelineEntry(
                ts=finding.created_at,
                kind="finding",
                title=f"finding {finding.severity} : {finding.title}",
                detail=finding.evidence[:300],
                actor_kind="agent",
                ref_id=finding.id,
            )
        )
    return sorted(entries, key=lambda e: e.ts)
