# SPDX-License-Identifier: Apache-2.0
"""Runs : détail, journal ACP (SSE), transcript, diff."""

from __future__ import annotations

import json
from typing import Annotated, Any

from choregos_core import matches_any, rapport_d_acces
from fastapi import APIRouter, Header, Query, Request
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from ..db.models import CostLedger, Project, Run, RunEvent, WorkItem
from ..deps import Db, Me, resolve_project
from ..errors import forbidden, not_found
from ..events import get_bus, sse_format
from ..knowledge.marked import readable, require_readable
from ..rbac import Permission
from ..schemas import ArtifactRef, DiffFileDto, DiffSummaryDto, RunDto, RunEventDto, RunLogsDto
from ..services import run_dto, tool_connectors_of

router = APIRouter(tags=["runs"])


async def _run(session: Any, run_id: str, principal: Any) -> tuple[Run, Project]:
    run = await session.get(Run, run_id)
    if run is None:
        raise not_found("Run", run_id)
    project = await session.get(Project, run.project_id)
    if project is None:
        raise not_found("Project", run.project_id)
    _, org_slug = await resolve_project(session, project.id)
    if not principal.can(Permission.PROJECT_READ, org_slug, project.slug):
        raise forbidden()
    item = await session.get(WorkItem, run.work_item_id)
    # Under a marked work item (ADR 0114) the run does not exist for a caller who may not read it.
    if item is not None and not await readable(session, principal, item, project.org_id):
        raise not_found("Run", run_id)
    return run, project


@router.get("/work-items/{id}/runs", response_model=list[RunDto], operation_id="listRuns")
async def list_runs(id: str, session: Db, principal: Me) -> list[RunDto]:
    item = await session.get(WorkItem, id)
    if item is None:
        item = (
            await session.execute(select(WorkItem).where(WorkItem.tracker_key == id).limit(1))
        ).scalar_one_or_none()
    if item is None:
        raise not_found("Work item", id)
    project = await session.get(Project, item.project_id)
    _, org_slug = await resolve_project(session, item.project_id)
    if project is None or not principal.can(Permission.PROJECT_READ, org_slug, project.slug):
        raise forbidden()
    await require_readable(session, principal, item, project.org_id)
    rows = (
        (await session.execute(select(Run).where(Run.work_item_id == item.id).order_by(Run.created_at)))
        .scalars()
        .all()
    )
    return [run_dto(row, project.slug) for row in rows]


@router.get("/runs/{id}", response_model=RunDto, operation_id="getRun")
async def get_run(id: str, session: Db, principal: Me) -> RunDto:
    run, project = await _run(session, id, principal)
    return run_dto(run, project.slug, await tool_connectors_of(session, run, project))


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


@router.get("/runs/{id}/access", operation_id="getRunAccess")
async def run_access(id: str, session: Db, principal: Me) -> dict[str, Any]:
    """Ce à quoi l'agent a touché : fichiers, commandes, réseau, outils — et les refus.

    Rien de nouveau n'est collecté : c'est le journal du run, replié. Un journal que
    personne ne lit n'est pas un audit, et deux agents produisent deux cents événements
    en quelques minutes.

    Les appels au catalogue d'outils sont ajoutés depuis le registre de coûts : ils portent
    ce qu'ils ont coûté, et une dépense fait partie de ce à quoi on a touché.
    """
    run, _ = await _run(session, id, principal)
    rows = (
        (await session.execute(select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.seq)))
        .scalars()
        .all()
    )
    appels = (
        (
            await session.execute(
                select(CostLedger).where(CostLedger.run_id == run.id, CostLedger.kind == "tool")
            )
        )
        .scalars()
        .all()
    )
    evenements: list[dict[str, Any]] = [{"type": r.type, "payload": r.payload} for r in rows]
    evenements += [
        {"type": "choregos.tool.called", "payload": {"tool": a.model, "provider": a.provider}} for a in appels
    ]
    rapport = rapport_d_acces(evenements)
    tool_cost_eur = round(sum(a.cost_eur for a in appels), 4)
    record: dict[str, Any] = {
        **rapport.to_dict(),
        "tool_cost_eur": tool_cost_eur,
    }
    if run.inputs_from is not None or run.initiated_by is not None:
        record["chain"] = await _chain(session, run)
    return record


#: How far back a chain is walked: a work item's steps, not an unbounded graph.
CHAIN_MAX_RUNS = 64


async def _chain(session: Any, run: Run) -> dict[str, Any]:
    """`initiator → run A → run B` (ADR 0077 §2): the runs this one read from, and theirs, earliest first.

    Only what the orchestrator recorded at each stage start (`runs.initiated_by`, `runs.inputs_from`),
    nothing the agent said. The walk stays within the run's work item, where its inputs live.
    """
    found: dict[str, Run] = {run.id: run}
    pending = list(run.inputs_from or [])
    while pending and len(found) < CHAIN_MAX_RUNS:
        wanted = [run_id for run_id in dict.fromkeys(pending) if run_id not in found]
        pending = []
        if not wanted:
            break
        query = select(Run).where(Run.id.in_(wanted), Run.work_item_id == run.work_item_id)
        for row in (await session.execute(query)).scalars().all():
            found[row.id] = row
            pending += list(row.inputs_from or [])
    earlier = sorted(
        (r for r in found.values() if r.id != run.id),
        key=lambda r: (r.started_at or r.created_at, r.id),
    )
    return {
        "initiated_by": run.initiated_by,
        "runs": [
            {
                "run_id": r.id,
                "stage": r.stage_role,
                "agent_slug": r.agent_slug,
                "agent_version": r.agent_version,
                "inputs_from": list(r.inputs_from or []),
            }
            for r in [*earlier, run]
        ],
    }


@router.get("/runs/{id}/transcript", response_model=ArtifactRef, operation_id="getRunTranscript")
async def transcript(id: str, session: Db, principal: Me) -> ArtifactRef:
    """The run's transcript: its ACP journal as JSONL, served from what the API stored (S36-01).

    The runner used to announce `transcript.jsonl` next to the context pack in the object
    store, and nothing ever uploaded it: the link led nowhere. The journal is already here,
    batched by the runner and idempotent by `seq`; it is the transcript, so it is served
    inline. `url` names where the same lines can be read as JSON.
    """
    run, _ = await _run(session, id, principal)
    rows = (
        (await session.execute(select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.seq)))
        .scalars()
        .all()
    )
    if not rows:
        raise not_found("Transcript", id)
    lines = [RunEventDto(seq=r.seq, type=r.type, ts=r.ts, payload=r.payload).model_dump_json() for r in rows]
    body = "\n".join(lines) + "\n"
    return ArtifactRef(
        url=f"/api/v1/runs/{run.id}/events",
        content_type="application/x-ndjson",
        size_bytes=len(body.encode()),
        inline=body,
    )


@router.get("/runs/{id}/logs", response_model=RunLogsDto, operation_id="getRunLogs")
async def run_logs(id: str, session: Db, principal: Me) -> RunLogsDto:
    """The runner pod's log tail and how the pod ended, redacted (S36-02, S36-03).

    The API never reads the cluster: while the pod runs, the orchestrator refreshes a live
    snapshot at each poll; once the run ends it writes the final archive. Both live in
    `runs.log_archive`, under the run's row-level security like the rest of the run.
    """
    run, _ = await _run(session, id, principal)
    archive: dict[str, Any] = run.log_archive or {}
    if not archive:
        return RunLogsDto(source="none", state=run.status)
    return RunLogsDto(
        source="archive" if archive.get("final", True) else "live",
        state=str(archive.get("state") or run.status),
        exit_code=archive.get("exit_code"),
        reason=archive.get("reason"),
        message=str(archive.get("message") or ""),
        lines=[str(line) for line in archive.get("lines") or []],
        truncated=bool(archive.get("truncated")),
        error=archive.get("error"),
        captured_at=archive.get("archived_at"),
    )


@router.get("/runs/{id}/diff", response_model=DiffSummaryDto, operation_id="getRunDiff")
async def diff(id: str, session: Db, principal: Me) -> DiffSummaryDto:
    """Diff du run, annoté : chaque fichier est marqué dans ou hors du périmètre autorisé."""
    run, _project = await _run(session, id, principal)
    result = run.result or {}
    reports: dict[str, Any] = (result.get("artifacts") or {}).get("reports", {})
    raw = reports.get("diff_files")
    files_raw: list[dict[str, Any]] = json.loads(raw) if isinstance(raw, str) else (raw or [])
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
