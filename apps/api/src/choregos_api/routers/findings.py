"""Findings : liste et triage (ticket, doublon, ignoré, agent-ready, faux positif)."""

from __future__ import annotations

from typing import Annotated

from choregos_contracts import EventType
from fastapi import APIRouter, Query
from sqlalchemy import select

from ..audit import record
from ..db.models import Finding, Project, WorkItem
from ..deps import Db, Me, Pagination, ProjectCtx, resolve_project
from ..errors import forbidden, not_found
from ..rbac import Permission
from ..schemas import FindingAction, FindingDto, FindingPage, PageMeta
from ..services import persist_event
from ..temporal import get_temporal, interpreter_id

router = APIRouter(tags=["findings"])


async def _dto(session: Db, row: Finding, project_slug: str) -> FindingDto:
    origin = await session.get(WorkItem, row.origin_work_item_id) if row.origin_work_item_id else None
    created = await session.get(WorkItem, row.created_work_item_id) if row.created_work_item_id else None
    return FindingDto(
        id=row.id,
        project_slug=project_slug,
        origin_work_item_key=origin.tracker_key if origin else None,
        origin_run_id=row.origin_run_id,
        title=row.title,
        type=row.type,
        severity=row.severity,
        evidence=row.evidence,
        suggested_fix=row.suggested_fix,
        estimate=row.estimate,
        status=row.status,
        created_work_item_key=created.tracker_key if created else None,
        duplicate_of=row.duplicate_of,
        occurrences=row.occurrences,
        relevant=row.relevant,
        created_at=row.created_at,
    )


@router.get("/projects/{id}/findings", response_model=FindingPage, operation_id="listFindings")
async def list_findings(
    ctx: ProjectCtx,
    session: Db,
    page: Pagination,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    severity: Annotated[str | None, Query()] = None,
) -> FindingPage:
    query = select(Finding).where(Finding.project_id == ctx.id)
    if status_filter:
        query = query.where(Finding.status == status_filter)
    if severity:
        query = query.where(Finding.severity == severity)
    rows = (await session.execute(query.order_by(Finding.created_at.desc()))).scalars().all()
    window, cursor, has_more = page.slice(list(rows))
    return FindingPage(
        items=[await _dto(session, row, ctx.slug) for row in window],
        meta=PageMeta(next_cursor=cursor, has_more=has_more),
    )


@router.post("/findings/{id}/actions", response_model=FindingDto, operation_id="postFindingAction")
async def act(id: str, body: FindingAction, session: Db, principal: Me) -> FindingDto:
    """Le triage humain : créer le ticket, marquer doublon, ignorer, rendre agent-ready."""
    row = await session.get(Finding, id)
    if row is None:
        raise not_found("Finding", id)
    project = await session.get(Project, row.project_id)
    if project is None:
        raise not_found("Projet", row.project_id)
    _, org_slug = await resolve_project(session, project.id)
    if not principal.can(Permission.FINDING_TRIAGE, org_slug, project.slug):
        raise forbidden()

    if body.action == "create_ticket":
        await get_temporal().signal(
            f"findings-{project.slug}", "create_ticket", {"finding_id": row.id, "by": principal.email}
        )
        row.status = "created"
    elif body.action == "mark_duplicate":
        row.status = "duplicate"
        row.duplicate_of = body.duplicate_of
    elif body.action == "dismiss":
        row.status = "dismissed"
        row.relevant = False
    elif body.action == "agent_ready":
        item = await session.get(WorkItem, row.created_work_item_id) if row.created_work_item_id else None
        if item is None:
            raise not_found("Ticket créé pour ce finding", row.id)
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
    elif body.action == "mark_relevant":
        row.relevant = True
    elif body.action == "mark_false_positive":
        row.relevant = False

    await persist_event(
        session,
        EventType.FINDING_DISMISSED if row.status == "dismissed" else EventType.FINDING_CREATED,
        project_id=project.id,
        project_slug=project.slug,
        subject=row.id,
        finding_id=row.id,
        action=body.action,
        by=principal.email,
    )
    await record(
        session,
        principal,
        f"finding.{body.action}",
        org_id=project.org_id,
        target_type="finding",
        target_id=row.id,
    )
    return await _dto(session, row, project.slug)
