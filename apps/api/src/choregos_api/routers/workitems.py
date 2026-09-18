"""Work items : liste, détail, timeline, décisions humaines, actions de contrôle."""

from __future__ import annotations

from typing import Annotated, Any

from choregos_contracts import Control, EventType, HumanDecision, HumanRequestKind
from choregos_core import utcnow
from fastapi import APIRouter, Query, status
from sqlalchemy import select

from ..audit import record
from ..db.models import Event, Finding, HumanRequest, Project, Run, WorkItem
from ..deps import Db, Me, Pagination, ProjectCtx, resolve_project
from ..errors import conflict, forbidden, not_found
from ..rbac import Permission
from ..schemas import (
    DecisionRequest,
    HumanRequestDto,
    PageMeta,
    TimelineEntry,
    WorkItemAction,
    WorkItemDto,
    WorkItemPage,
)
from ..services import human_request_dto, persist_event, work_item_dto
from ..temporal import deliver_control, deliver_decision, get_temporal, interpreter_id

router = APIRouter(tags=["work-items"])


async def _load(session: Any, item_id: str) -> tuple[WorkItem, Project]:
    item = await session.get(WorkItem, item_id)
    if item is None:
        item = (
            await session.execute(select(WorkItem).where(WorkItem.tracker_key == item_id).limit(1))
        ).scalar_one_or_none()
    if item is None:
        raise not_found("Ticket", item_id)
    project = await session.get(Project, item.project_id)
    if project is None:
        raise not_found("Projet", item.project_id)
    return item, project


@router.get("/projects/{id}/work-items", response_model=WorkItemPage, operation_id="listWorkItems")
async def list_work_items(
    ctx: ProjectCtx,
    session: Db,
    page: Pagination,
    state: Annotated[str | None, Query()] = None,
    size: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query()] = None,
) -> WorkItemPage:
    query = select(WorkItem).where(WorkItem.project_id == ctx.id)
    if state:
        query = query.where(WorkItem.state == state)
    if size:
        query = query.where(WorkItem.size == size)
    rows = (await session.execute(query.order_by(WorkItem.created_at.desc()))).scalars().all()
    if q:
        needle = q.lower()
        rows = [r for r in rows if needle in r.title.lower() or needle in r.tracker_key.lower()]
    window, cursor, has_more = page.slice(list(rows))
    return WorkItemPage(
        items=[await work_item_dto(session, item, ctx.project) for item in window],
        meta=PageMeta(next_cursor=cursor, has_more=has_more),
    )


@router.get("/work-items/{id}", response_model=WorkItemDto, operation_id="getWorkItem")
async def get_work_item(id: str, session: Db, principal: Me) -> WorkItemDto:
    item, project = await _load(session, id)
    _, org_slug = await resolve_project(session, project.id)
    if not principal.can(Permission.PROJECT_READ, org_slug, project.slug):
        raise forbidden()
    return await work_item_dto(session, item, project)


@router.get(
    "/work-items/{id}/timeline", response_model=list[TimelineEntry], operation_id="getWorkItemTimeline"
)
async def timeline(id: str, session: Db, principal: Me) -> list[TimelineEntry]:
    """États, runs, décisions, findings : l'histoire complète d'un ticket, dans l'ordre."""
    item, project = await _load(session, id)
    _, org_slug = await resolve_project(session, project.id)
    if not principal.can(Permission.PROJECT_READ, org_slug, project.slug):
        raise forbidden()

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


@router.post(
    "/work-items/{id}/decisions",
    response_model=HumanRequestDto,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="postDecision",
)
async def post_decision(id: str, body: DecisionRequest, session: Db, principal: Me) -> HumanRequestDto:
    """Enregistre la décision, la transmet au workflow, et ferme la demande humaine."""
    item, project = await _load(session, id)
    _, org_slug = await resolve_project(session, project.id)
    if not principal.can(Permission.ITEM_DECIDE, org_slug, project.slug):
        raise forbidden("décider demande au moins le rôle developer")

    query = select(HumanRequest).where(
        HumanRequest.work_item_id == item.id, HumanRequest.decided_at.is_(None)
    )
    if body.request_id:
        query = select(HumanRequest).where(HumanRequest.id == body.request_id)
    request_row = (
        await session.execute(query.order_by(HumanRequest.requested_at.desc()).limit(1))
    ).scalar_one_or_none()
    if request_row is None:
        raise conflict("aucune demande humaine en attente sur ce ticket")
    if request_row.decided_at is not None:
        raise conflict("cette demande a déjà été tranchée")

    kind_map = {
        "approve": HumanRequestKind.APPROVAL,
        "reject": HumanRequestKind.APPROVAL,
        "answer": HumanRequestKind.QUESTION,
        "scope_change": HumanRequestKind.SCOPE_CHANGE,
    }
    decision = HumanDecision(
        request_id=request_row.id,
        kind=kind_map[body.kind],
        approved=body.kind in {"approve", "scope_change"} if body.kind != "reject" else False,
        answer=body.answer,
        granted_paths=body.granted_paths,
        reason=body.reason,
        decided_by=principal.email,
        channel="web",
    )
    request_row.decided_at = utcnow()
    request_row.decided_by = principal.email
    request_row.decision = decision.model_dump(mode="json")

    await deliver_decision(project.slug, item.tracker_key, decision)
    await persist_event(
        session,
        EventType.WORKITEM_HUMAN_DECIDED,
        project_id=project.id,
        work_item_id=item.id,
        project_slug=project.slug,
        subject=item.tracker_key,
        kind=body.kind,
        by=principal.email,
    )
    await record(
        session, principal, "workitem.decision", target_type="work_item", target_id=item.id, kind=body.kind
    )
    return human_request_dto(request_row)


@router.post(
    "/work-items/{id}/actions", status_code=status.HTTP_202_ACCEPTED, operation_id="postWorkItemAction"
)
async def post_action(id: str, body: WorkItemAction, session: Db, principal: Me) -> dict[str, str]:
    """pause, resume, stop, migrate, rerun_stage, mark_agent_ready."""
    item, project = await _load(session, id)
    _, org_slug = await resolve_project(session, project.id)
    if not principal.can(Permission.ITEM_CONTROL, org_slug, project.slug):
        raise forbidden("contrôler un ticket demande au moins le rôle developer")

    if body.action == "mark_agent_ready":
        payload = {
            "project_id": project.id,
            "project_slug": project.slug,
            "work_item_id": item.id,
            "tracker_key": item.tracker_key,
        }
        workflow_id = interpreter_id(project.slug, item.tracker_key)
        await get_temporal().start_interpreter(workflow_id, payload)
        item.temporal_wf_id = workflow_id
    else:
        control = Control(
            action=body.action,
            workflow_def_id=body.workflow_def_id,
            state_mapping=body.state_mapping,
            run_id=body.run_id,
            reason=body.reason,
            by=principal.email,
        )
        await deliver_control(project.slug, item.tracker_key, control)
        if body.action == "pause":
            item.paused = True
        elif body.action in {"resume", "stop"}:
            item.paused = False

    await record(
        session,
        principal,
        f"workitem.{body.action}",
        target_type="work_item",
        target_id=item.id,
        reason=body.reason,
    )
    return {"status": "accepted", "action": body.action}
