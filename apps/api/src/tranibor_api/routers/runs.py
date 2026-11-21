# SPDX-License-Identifier: Apache-2.0
"""Runs: detail, ACP log (SSE), transcript, diff, what fills the context."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request, status
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse
from tranibor_core import access_report, matches_any
from tranibor_core.agent_tree import NodeStatus, agent_nodes
from tranibor_core.context_breakdown import EVENT as CONTEXT_BREAKDOWN
from tranibor_core.domain import aware, utcnow
from tranibor_core.tool_ledger import IN_FLIGHT

from ..db.models import CostLedger, Project, Run, RunEvent, WorkItem
from ..db.session import ALL_ORGS, session_scope
from ..deps import Db, Me, Page, Pagination, resolve_project
from ..errors import forbidden, not_found
from ..events import get_bus, sse_format
from ..keyset import keyset_page
from ..knowledge.marked import readable, require_readable
from ..rbac import Permission
from ..schemas import (
    ArtifactRef,
    DiffFileDto,
    DiffSummaryDto,
    RunAgentList,
    RunAgentNode,
    RunContextDto,
    RunContextPoint,
    RunDto,
    RunEventDto,
    RunEventPage,
    RunLogsDto,
    RunPage,
    StopRequest,
)
from ..services import run_dto, tool_connectors_of
from ..services.context_breakdown import MEASURED_BACKENDS, breakdown_of
from ..services.model_catalogue import max_input_tokens_of
from ..services.run_cancel import cancel_run_now

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


@router.get("/work-items/{id}/runs", response_model=RunPage, operation_id="listRuns")
async def list_runs(id: str, session: Db, principal: Me, page: Pagination) -> RunPage:
    """The runs of a work item, oldest first, one page at a time (ADR 0206): the order the journey reads."""
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
    rows, meta = await keyset_page(
        session,
        select(Run).where(Run.work_item_id == item.id),
        sort=Run.created_at,
        tiebreak=Run.id,
        page=page,
        descending=False,
    )
    return RunPage(items=[run_dto(row, project.slug) for row in rows], meta=meta)


@router.get("/runs/{id}", response_model=RunDto, operation_id="getRun")
async def get_run(id: str, session: Db, principal: Me) -> RunDto:
    run, project = await _run(session, id, principal)
    return run_dto(run, project.slug, await tool_connectors_of(session, run, project))


@router.post(
    "/runs/{id}/cancel",
    response_model=RunDto,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="cancelRun",
)
async def cancel_run(id: str, body: StopRequest, session: Db, principal: Me) -> RunDto:
    """Stop this run now, with a reason (ADR 0142 §2): its result is written as stopped by the caller
    unless one is written already (409, saying how it ended), and its executor is stopped once that
    is committed. Needs `item:control` on the run's project; audited as `run.cancel`."""
    run, project = await _run(session, id, principal)
    stopped = await cancel_run_now(session, principal, run, body.reason)
    return run_dto(stopped, project.slug)


#: How many stored events one read takes while the backlog of a stream is sent (the page limit's ceiling).
_BACKLOG_CHUNK = 200


@router.get("/runs/{id}/events", response_model=RunEventPage, operation_id="getRunEvents")
async def run_events(
    request: Request,
    id: str,
    session: Db,
    principal: Me,
    page: Pagination,
    after_seq: Annotated[int | None, Query()] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> Any:
    """The run's ACP log, oldest first (the order of `seq`), one page at a time (ADR 0206). Over SSE,
    the stored events are sent in chunks and then the live ones, resuming after `Last-Event-ID` (S6-04).

    A client that follows the log (`choregos live`, the console) passes `after_seq` for what it has not
    read and `cursor` for the rest of that read: the cursor is stable while events are appended.
    """
    run, _ = await _run(session, id, principal)
    run_id = run.id
    if "text/event-stream" not in request.headers.get("accept", ""):
        query = select(RunEvent).where(RunEvent.run_id == run_id)
        if after_seq is not None:
            query = query.where(RunEvent.seq > after_seq)
        rows, meta = await keyset_page(
            session, query, sort=RunEvent.seq, tiebreak=RunEvent.id, page=page, descending=False
        )
        return RunEventPage(
            items=[RunEventDto(seq=r.seq, type=r.type, ts=r.ts, payload=r.payload) for r in rows],
            meta=meta,
        )

    bus = get_bus()

    async def stream() -> Any:
        # The request's session is closed once the route returns, before this generator runs: the backlog
        # is read with its own, after the permission check above.
        cursor: str | None = None
        while True:
            async with session_scope(orgs=ALL_ORGS) as reading:
                query = select(RunEvent).where(RunEvent.run_id == run_id)
                if after_seq is not None:
                    query = query.where(RunEvent.seq > after_seq)
                rows, meta = await keyset_page(
                    reading,
                    query,
                    sort=RunEvent.seq,
                    tiebreak=RunEvent.id,
                    page=Page(cursor=cursor, limit=_BACKLOG_CHUNK),
                    descending=False,
                )
                chunk = [RunEventDto(seq=r.seq, type=r.type, ts=r.ts, payload=r.payload) for r in rows]
            for stored_event in chunk:
                yield {
                    "id": f"seq-{stored_event.seq}",
                    "event": "run.event",
                    "data": stored_event.model_dump_json(),
                }
            if not meta.has_more:
                break
            cursor = meta.next_cursor
        async for live_event in bus.subscribe(f"run:{run_id}", last_event_id):
            yield sse_format(live_event)

    return EventSourceResponse(stream())


@router.get(
    "/runs/{id}/context-breakdown", response_model=RunContextDto, operation_id="getRunContextBreakdown"
)
async def context_breakdown(id: str, session: Db, principal: Me) -> RunContextDto:
    """What fills the run's context (ADR 0141 §6): its last measured call and the series of kept
    totals, read from its `context.breakdown` events, with the run's own read right. A breakdown the
    runner or the gateway left without the model's window gets it from the organisation's catalogue.
    An ACP CLI's run is measurable once the gateway's callback kept a breakdown of it (S64-08b)."""
    run, project = await _run(session, id, principal)
    payloads = (
        (
            await session.execute(
                select(RunEvent.payload)
                .where(RunEvent.run_id == run.id, RunEvent.type == CONTEXT_BREAKDOWN)
                .order_by(RunEvent.seq)
            )
        )
        .scalars()
        .all()
    )
    kept = [breakdown for payload in payloads if (breakdown := breakdown_of(payload)) is not None]
    latest = kept[-1] if kept else None
    if latest is not None and latest.max_input_tokens is None:
        window = await max_input_tokens_of(session, project.org_id, latest.model)
        latest = latest.model_copy(update={"max_input_tokens": window})
    return RunContextDto(
        run_id=run.id,
        measurable=run.backend in MEASURED_BACKENDS or bool(kept),
        backend=run.backend,
        latest=latest,
        series=[RunContextPoint(call=b.call, at=b.at, prompt_tokens=b.prompt_tokens) for b in kept],
    )


#: A run's status, as a node's: what is neither over nor failed is still running.
_NODE_STATUS: dict[str, NodeStatus] = {
    "succeeded": "completed",
    "failed": "failed",
    "cancelled": "failed",
    "timed_out": "failed",
}


def _stamp(value: str | None) -> datetime | None:
    return aware(datetime.fromisoformat(value)) if value else None


@router.get("/runs/{id}/agents", response_model=RunAgentList, operation_id="listRunAgents")
async def run_agents(id: str, session: Db, principal: Me) -> RunAgentList:
    """The run's agent and the sub-agents it delegated to (ADR 0154 §5, G5), derived from its journal with
    the backend's own rule (`tranibor_core.agent_tree`). A backend that sends no parent id gives the run's
    agent alone, its calls flat; a surface reads these nodes and never the journal's `_meta`."""
    run, _ = await _run(session, id, principal)
    rows = (
        (
            await session.execute(
                select(RunEvent).where(RunEvent.run_id == run.id, RunEvent.type == "session/update")
            )
        )
        .scalars()
        .all()
    )
    started = aware(run.started_at or run.created_at) or utcnow()
    nodes = agent_nodes(
        run.backend or "",
        [{"seq": r.seq, "type": r.type, "ts": aware(r.ts), "payload": r.payload} for r in rows],
        root_id=run.id,
        root_status=_NODE_STATUS.get(run.status, "running"),
        root_started=started.isoformat(),
        root_ended=ended.isoformat() if (ended := aware(run.ended_at)) else None,
    )
    return RunAgentList(
        items=[
            RunAgentNode(
                id=n.id,
                parent_id=n.parent_id,
                kind=n.kind,
                tool=n.tool,
                status=n.status,
                started_at=_stamp(n.started_at) or started,
                ended_at=_stamp(n.ended_at),
                tool_calls=n.tool_calls,
            )
            for n in nodes
        ]
    )


@router.get("/runs/{id}/access", operation_id="getRunAccess")
async def run_access(id: str, session: Db, principal: Me) -> dict[str, Any]:
    """What the agent touched: files, commands, network, tools, and the refusals.

    Nothing new is collected: this is the run's log, folded. A log that nobody reads is not an
    audit, and two agents produce two hundred events in a few minutes.

    Calls to the tool catalogue are added from the cost ledger: they carry what they cost, and a
    spend is part of what was touched. A place still held under the tool-call cap is not a call yet
    (S57-03): it is left out until the call answers.
    """
    run, _ = await _run(session, id, principal)
    rows = (
        (await session.execute(select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.seq)))
        .scalars()
        .all()
    )
    calls = (
        (
            await session.execute(
                select(CostLedger).where(
                    CostLedger.run_id == run.id, CostLedger.kind == "tool", CostLedger.provider != IN_FLIGHT
                )
            )
        )
        .scalars()
        .all()
    )
    events: list[dict[str, Any]] = [{"type": r.type, "payload": r.payload} for r in rows]
    events += [
        {"type": "choregos.tool.called", "payload": {"tool": a.model, "provider": a.provider}} for a in calls
    ]
    report = access_report(events)
    tool_cost_eur = round(sum(a.cost_eur for a in calls), 4)
    record: dict[str, Any] = {
        **report.to_dict(),
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
    """The run's diff, annotated: each file is marked inside or outside the allowed scope."""
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
