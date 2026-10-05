# SPDX-License-Identifier: Apache-2.0
"""Projections des tickets, runs, demandes humaines et releases ; la clé d'un ticket interne ;
le rangement des sorties d'une étape."""

from __future__ import annotations

from typing import Any

from choregos_contracts import EventType
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import (
    Connector,
    Event,
    Finding,
    HumanRequest,
    Project,
    Release,
    Run,
    WorkItem,
)
from ..errors import conflict
from ..rbac import Principal
from ..schemas import (
    HumanRequestDto,
    ReleaseDto,
    RunDto,
    RunSummary,
    TimelineEntry,
    Totals,
    WorkflowFailure,
    WorkItemCreate,
    WorkItemDto,
)
from ..temporal import get_temporal, interpreter_id
from .couts import estimate_cost
from .definitions import default_workflow, workflow_du_ticket, workflow_model
from .evenements import persist_event


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


async def cle_de_ticket_interne(session: AsyncSession, project: Project) -> str:
    """Attribue `<PRÉFIXE>-<n>` quand la plateforme tient elle-même les tickets (`tracker: internal`).

    Compter en base plutôt que tenir un compteur évite un deuxième endroit où l'état vit ;
    la course entre deux créations simultanées est laissée à la contrainte d'unicité
    `(project_id, tracker_key)`. Servait à l'orchestrateur (findings) ; sert aussi à l'API
    depuis que `POST /projects/{id}/work-items` existe.
    """
    connecteur = (
        await session.execute(
            select(Connector).where(Connector.project_id == project.id, Connector.kind == "tracker")
        )
    ).scalar_one_or_none()
    configure = (connecteur.config or {}).get("key_prefix") if connecteur else None
    prefixe = str(configure or project.slug).upper()
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


async def creer_un_ticket(
    session: AsyncSession,
    principal: Principal,
    project: Project,
    demande: WorkItemCreate,
    *,
    canal: str = "api",
) -> WorkItem:
    """Pose une demande dans Choregos — seulement quand le tracker est interne.

    Le seul chemin de création d'un ticket interne : la route REST et la porte MCP (ADR 0030)
    l'empruntent toutes deux, pour qu'un ticket créé depuis Claude ne diffère en rien d'un ticket
    créé depuis la console. La clé est frappée par la plateforme (`<PRÉFIXE>-<n>`), l'état initial
    est celui du workflow du projet, et l'interpréteur démarre tout de suite sauf `start=False`.
    """
    if not await le_tracker_est_interne(session, project):
        raise conflict("ce projet reçoit ses tickets d'un tracker externe : créez la demande là-bas")
    from choregos_core import WorkflowEngine

    ligne = await default_workflow(session, project.id)
    workflow = workflow_model(ligne)
    key = await cle_de_ticket_interne(session, project)
    item = WorkItem(
        project_id=project.id,
        tracker_key=key,
        title=demande.title,
        body_snapshot=demande.body,
        size=demande.size,
        risk=demande.risk,
        state=WorkflowEngine(workflow).initial_state,
        # Épinglé à sa naissance (ADR 0031) : une version publiée ensuite ne le déplace pas.
        workflow_def_id=ligne.id if ligne is not None else None,
        created_by=principal.email,
        allowed_paths=[],
    )
    session.add(item)
    await session.flush()
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
        workflow_id = interpreter_id(project.slug, key)
        await get_temporal().start_interpreter(
            workflow_id,
            {
                "project_id": project.id,
                "project_slug": project.slug,
                "work_item_id": item.id,
                "tracker_key": key,
            },
        )
        item.temporal_wf_id = workflow_id
    await record(
        session,
        principal,
        "workitem.create",
        org_id=project.org_id,
        target_type="work_item",
        target_id=item.id,
        key=key,
        canal=canal,
    )
    return item


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
    for req in requests:
        entries.append(
            TimelineEntry(
                ts=req.requested_at,
                kind="decision",
                title=f"demande humaine : {req.kind}",
                detail=str(req.payload.get("question") or req.payload.get("summary") or ""),
                actor=req.decided_by,
                actor_kind="user",
                ref_id=req.id,
            )
        )
        if req.decided_at:
            entries.append(
                TimelineEntry(
                    ts=req.decided_at,
                    kind="decision",
                    title=f"décision : {(req.decision or {}).get('kind', 'répondu')}",
                    detail=str((req.decision or {}).get("reason") or ""),
                    actor=req.decided_by,
                    actor_kind="user",
                    ref_id=req.id,
                )
            )
    events = (
        (await session.execute(select(Event).where(Event.work_item_id == item.id).order_by(Event.ts)))
        .scalars()
        .all()
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
