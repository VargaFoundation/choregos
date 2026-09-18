"""Runs : détail, journal ACP (SSE), transcript, diff."""

from __future__ import annotations

from typing import Annotated, Any

from choregos_core import matches_any
from fastapi import APIRouter, Header, Query, Request
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from ..db.models import Project, Run, RunEvent, WorkItem
from ..deps import Db, Me, resolve_project
from ..errors import forbidden, not_found
from ..events import get_bus, sse_format
from ..rbac import Permission
from ..schemas import ArtifactRef, DiffFileDto, DiffSummaryDto, RunDto, RunEventDto
from ..services import run_dto

router = APIRouter(tags=["runs"])


async def _run(session: Any, run_id: str, principal: Any) -> tuple[Run, Project]:
    run = await session.get(Run, run_id)
    if run is None:
        raise not_found("Run", run_id)
    project = await session.get(Project, run.project_id)
    if project is None:
        raise not_found("Projet", run.project_id)
    _, org_slug = await resolve_project(session, project.id)
    if not principal.can(Permission.PROJECT_READ, org_slug, project.slug):
        raise forbidden()
    return run, project


@router.get("/work-items/{id}/runs", response_model=list[RunDto], operation_id="listRuns")
async def list_runs(id: str, session: Db, principal: Me) -> list[RunDto]:
    item = await session.get(WorkItem, id)
    if item is None:
        item = (
            await session.execute(select(WorkItem).where(WorkItem.tracker_key == id).limit(1))
        ).scalar_one_or_none()
    if item is None:
        raise not_found("Ticket", id)
    project = await session.get(Project, item.project_id)
    _, org_slug = await resolve_project(session, item.project_id)
    if project is None or not principal.can(Permission.PROJECT_READ, org_slug, project.slug):
        raise forbidden()
    rows = (
        (await session.execute(select(Run).where(Run.work_item_id == item.id).order_by(Run.created_at)))
        .scalars()
        .all()
    )
    return [run_dto(row, project.slug) for row in rows]


@router.get("/runs/{id}", response_model=RunDto, operation_id="getRun")
async def get_run(id: str, session: Db, principal: Me) -> RunDto:
    run, project = await _run(session, id, principal)
    return run_dto(run, project.slug)


@router.get("/runs/{id}/events", operation_id="getRunEvents")
async def run_events(
    request: Request,
    id: str,
    session: Db,
    principal: Me,
    after_seq: Annotated[int | None, Query()] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> Any:
    """Journal ACP du run. En SSE, reprend après `Last-Event-ID` (S6-04)."""
    run, _ = await _run(session, id, principal)
    query = select(RunEvent).where(RunEvent.run_id == run.id)
    if after_seq is not None:
        query = query.where(RunEvent.seq > after_seq)
    rows = (await session.execute(query.order_by(RunEvent.seq))).scalars().all()
    stored = [RunEventDto(seq=r.seq, type=r.type, ts=r.ts, payload=r.payload) for r in rows]

    if "text/event-stream" not in request.headers.get("accept", ""):
        return stored

    bus = get_bus()

    async def stream() -> Any:
        for stored_event in stored:
            yield {
                "id": f"seq-{stored_event.seq}",
                "event": "run.event",
                "data": stored_event.model_dump_json(),
            }
        async for live_event in bus.subscribe(f"run:{run.id}", last_event_id):
            yield sse_format(live_event)

    return EventSourceResponse(stream())


@router.get("/runs/{id}/transcript", response_model=ArtifactRef, operation_id="getRunTranscript")
async def transcript(id: str, session: Db, principal: Me) -> ArtifactRef:
    run, _ = await _run(session, id, principal)
    if not run.transcript_url:
        raise not_found("Transcript", id)
    return ArtifactRef(url=run.transcript_url, content_type="application/x-ndjson")


@router.get("/runs/{id}/diff", response_model=DiffSummaryDto, operation_id="getRunDiff")
async def diff(id: str, session: Db, principal: Me) -> DiffSummaryDto:
    """Diff du run, annoté : chaque fichier est marqué dans ou hors du périmètre autorisé."""
    run, _project = await _run(session, id, principal)
    result = run.result or {}
    reports: dict[str, Any] = (result.get("artifacts") or {}).get("reports", {})
    files_raw = reports.get("diff_files") or []
    allowed = list(run.allowed_paths or [])
    files = [
        DiffFileDto(
            path=entry.get("path", ""),
            status=entry.get("status", "modified"),
            additions=int(entry.get("additions", 0)),
            deletions=int(entry.get("deletions", 0)),
            patch=entry.get("patch"),
            in_scope=matches_any(entry.get("path", ""), allowed) if allowed else True,
        )
        for entry in files_raw
    ]
    return DiffSummaryDto(
        base=reports.get("base"),
        head=(result.get("artifacts") or {}).get("branch"),
        files=files,
        additions=sum(f.additions for f in files),
        deletions=sum(f.deletions for f in files),
    )
