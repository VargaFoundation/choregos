# SPDX-License-Identifier: Apache-2.0
"""What is in flight in an organisation, as one list and a stream (ADR 0142 §1, ADR 0141 §2).

Runs `queued` or `running` and open agent sessions, newest first, then what ended in the last hour
(at most 50), so that a stop is seen to have taken effect. Nothing new is stored: a run's entry is
read from `runs` (its work item's key, its workflow's name, its events counted), a session's from
`agent_sessions` and its journal.

Who sees what is decided entry by entry, as each one's own page decides it: a run needs
`project:read` on its project and, under a marked work item (ADR 0114), every base of the mark; a
session is its opener's only (`routers/agent_sessions.py`). `can_stop` is `item:control` on the
project for a run (ADR 0142 §2), and always true for a session, whose reader is its owner.

A run's spend so far is the gateway's count for its key, never written to the ledger, read at most
every `SPEND_TTL_S` per run (`spend_so_far`); a gateway that fails, is slow, or a run without a key
gives `None`, never zeros. A run whose spend was booked shows the booked figures.

Conversation turns (`kind=turn`, S64-09c) are read by `activity_turns`: a conversation's person and its
assistant's auditors see them; the `assistant` filter (an installation's id) keeps only them, and a
conversation is never listed as a session. Runs and sessions belong to no assistant.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from choregos_contracts import ChoregosEvent, EventType
from choregos_core import utcnow
from choregos_core.domain import aware
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AgentSessionEvent, AgentSessionRecord, Project, Run, RunEvent, WorkflowDef, WorkItem
from ..knowledge.marked import readable
from ..logging import get_logger
from ..rbac import Permission
from ..schemas.activity import ActivityEntry, ActivityList, SpendSoFar
from .activity_turns import turn_entries
from .initiator import initiator

if TYPE_CHECKING:
    from choregos_adapters.base import GatewayAdapter

    from ..rbac import Principal

log = get_logger(__name__)

#: A run in one of these is in flight; any other status has ended.
IN_FLIGHT = ("queued", "running")
#: How far back a finished entry is still shown, and how many at most.
FINISHED_WINDOW = timedelta(hours=1)
FINISHED_MAX = 50
#: A bound on what one list reads of each kind: an organisation's concurrency is far below it.
IN_FLIGHT_MAX = 500
#: How often the gateway is asked for one entry's spend (ADR 0141 §2), and how long it may take.
SPEND_TTL_S = 30.0
GATEWAY_TIMEOUT_S = 5.0
#: Beyond this many cached spends, the stale ones are dropped.
_CACHE_PRUNE_AT = 2000

#: The run events the stream follows, and the one that says a run has ended.
RUN_EVENTS = frozenset(
    {EventType.RUN_QUEUED, EventType.RUN_STARTED, EventType.RUN_PROGRESS, EventType.RUN_FINISHED}
)


@dataclass(frozen=True, slots=True)
class ActivityFilters:
    project: str | None = None
    workflow: str | None = None
    agent: str | None = None
    assistant: str | None = None
    kind: str | None = None

    def wants(self, kind: str) -> bool:
        """Whether entries of this kind can match."""
        if self.kind is not None and self.kind != kind:
            return False
        if kind == "turn":
            # A turn belongs to a person's conversation: no project the filter names, no workflow.
            return self.project is None and self.workflow is None
        if self.assistant is not None:
            return False  # only a turn belongs to an assistant
        # A session belongs to no workflow.
        return not (kind == "session" and self.workflow is not None)

    def keeps(self, entry: ActivityEntry) -> bool:
        """The same filters, on one entry: what the stream applies to each event's entry."""
        return (
            self.wants(entry.kind)
            and (self.project is None or entry.project_slug == self.project)
            and (self.workflow is None or entry.workflow == self.workflow)
            and (self.agent is None or entry.agent_slug == self.agent)
            and (self.assistant is None or entry.assistant == self.assistant)
        )


@dataclass(frozen=True, slots=True)
class ActivityReader:
    """Who reads, in which organisation, with which filters."""

    principal: Principal
    org_id: str
    org_slug: str
    filters: ActivityFilters


# ───────────────────────────── spend so far ─────────────────────────────

#: `<kind>:<id>` -> (when the gateway was asked, by the monotonic clock; what it said, or `None`).
_spend_cache: dict[str, tuple[float, SpendSoFar | None]] = {}
#: The clock the cache reads; a test moves it.
clock: Callable[[], float] = time.monotonic


def forget_spends() -> None:
    """Empties the cache (tests)."""
    _spend_cache.clear()


async def spend_so_far(
    cache_key: str, key_id: str | None, gateway_of: Callable[[], Awaitable[GatewayAdapter]]
) -> SpendSoFar | None:
    """The gateway's count for `key_id`, asked at most once every `SPEND_TTL_S` per `cache_key`.

    `None` without a key, and when the gateway cannot be built, fails or is too slow: the screen says
    "—" (ADR 0141 §2). A failure is remembered as long as a success, so a gateway that is down is not
    asked again at each event of a busy run."""
    if not key_id:
        return None
    now = clock()
    cached = _spend_cache.get(cache_key)
    if cached is not None and now - cached[0] < SPEND_TTL_S:
        return cached[1]
    value: SpendSoFar | None
    try:
        gateway = await gateway_of()
        spend = await asyncio.wait_for(gateway.spend(key_id), timeout=GATEWAY_TIMEOUT_S)
        value = SpendSoFar(
            tokens_in=spend.tokens_in,
            tokens_out=spend.tokens_out,
            tokens_cached=spend.tokens_cached,
            cost_usd=spend.cost_usd,
            read_at=utcnow(),
        )
    except Exception as error:  # what the gateway cannot say is unknown, never zero
        log.warning("activity.spend_unknown", entry=cache_key, error=str(error)[:200])
        value = None
    _spend_cache[cache_key] = (now, value)
    if len(_spend_cache) > _CACHE_PRUNE_AT:
        for key, (asked, _) in list(_spend_cache.items()):
            if now - asked >= SPEND_TTL_S:
                _spend_cache.pop(key, None)
    return value


def _booked(run: Run) -> SpendSoFar | None:
    """What the ledger holds of a run whose spend was collected (`outcome.py` writes `run.tokens`)."""
    if not run.spend_collected:
        return None
    tokens = run.tokens or {}
    return SpendSoFar(
        tokens_in=int(tokens.get("tokens_in", 0)),
        tokens_out=int(tokens.get("tokens_out", 0)),
        tokens_cached=int(tokens.get("tokens_cached", 0)),
        cost_usd=float(run.cost_usd or 0.0),
        read_at=aware(run.ended_at or run.updated_at) or utcnow(),
        booked=True,
    )


class _Gateways:
    """One gateway adapter per project (a run's key lives on it) or per organisation (a session's),
    built only when a spend is not in the cache, at most once per request."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._built: dict[str, GatewayAdapter] = {}

    def of_project(self, project: Project) -> Callable[[], Awaitable[GatewayAdapter]]:
        async def build() -> GatewayAdapter:
            if f"project:{project.id}" not in self._built:
                from . import gateway

                self._built[f"project:{project.id}"] = await gateway.project_gateway(self.session, project)
            return self._built[f"project:{project.id}"]

        return build

    def of_sessions(self, org_id: str) -> Callable[[], Awaitable[GatewayAdapter]]:
        async def build() -> GatewayAdapter:
            if f"org:{org_id}" not in self._built:
                from .agent_sessions import session_gateway

                self._built[f"org:{org_id}"] = await session_gateway(self.session, org_id)
            return self._built[f"org:{org_id}"]

        return build


# ───────────────────────────── entries ─────────────────────────────


async def _steps(
    session: AsyncSession, model: Any, column: Any, ids: list[str]
) -> dict[str, tuple[int, str]]:
    """For each id, its events counted and the type of the last one (by `seq`)."""
    if not ids:
        return {}
    counted = (
        select(column.label("owner"), func.count().label("steps"), func.max(model.seq).label("last"))
        .where(column.in_(ids))
        .group_by(column)
        .subquery()
    )
    rows = await session.execute(
        select(counted.c.owner, counted.c.steps, model.type).join(
            model, (column == counted.c.owner) & (model.seq == counted.c.last)
        )
    )
    return {str(owner): (int(steps), str(last or "")) for owner, steps, last in rows.all()}


async def _run_entries(
    session: AsyncSession,
    reader: ActivityReader,
    rows: list[tuple[Run, WorkItem, str | None]],
    projects: dict[str, Project],
    gateways: _Gateways,
) -> list[ActivityEntry]:
    steps = await _steps(session, RunEvent, RunEvent.run_id, [run.id for run, _, _ in rows])
    entries = []
    for run, item, workflow in rows:
        if not await readable(session, reader.principal, item, reader.org_id):
            continue  # a marked work item's runs do not exist for this reader (ADR 0114)
        project = projects[run.project_id]
        in_flight = run.status in IN_FLIGHT
        spend = (
            await spend_so_far(f"run:{run.id}", run.gateway_key_id, gateways.of_project(project))
            if in_flight
            else _booked(run)
        )
        count, last = steps.get(run.id, (0, ""))
        entries.append(
            ActivityEntry(
                kind="run",
                id=run.id,
                status=run.status,
                project_slug=project.slug,
                work_item_id=item.id,
                work_item_key=item.tracker_key,
                workflow=workflow,
                stage_role=run.stage_role,
                agent_slug=run.agent_slug,
                model=run.model,
                initiated_by=run.initiated_by,
                started_at=aware(run.started_at or run.created_at) or utcnow(),
                ended_at=aware(run.ended_at),
                last_event=last or None,
                steps=count,
                spend=spend,
                can_stop=in_flight
                and reader.principal.can(Permission.ITEM_CONTROL, reader.org_slug, project.slug),
            )
        )
    return entries


async def _session_entries(
    session: AsyncSession,
    reader: ActivityReader,
    records: list[AgentSessionRecord],
    projects: dict[str, Project],
    gateways: _Gateways,
) -> list[ActivityEntry]:
    steps = await _steps(session, AgentSessionEvent, AgentSessionEvent.session_id, [r.id for r in records])
    entries = []
    for record in records:
        project = projects.get(record.project_id)
        is_open = record.status == "open"
        spend = (
            await spend_so_far(
                f"session:{record.id}", record.gateway_key_id, gateways.of_sessions(record.org_id)
            )
            if is_open
            else None
        )
        count, last = steps.get(record.id, (0, ""))
        entries.append(
            ActivityEntry(
                kind="session",
                id=record.id,
                status="open" if is_open else "closed",
                project_slug=project.slug if project else None,
                agent_slug=record.agent_slug,
                model=record.model or None,
                # Only its opener reads a session: the reader is who started it.
                initiated_by=initiator("person", reader.principal.email),
                started_at=aware(record.created_at) or utcnow(),
                ended_at=aware(record.closed_at),
                last_event=last or None,
                steps=count,
                spend=spend,
                can_stop=is_open,
            )
        )
    return entries


async def _readable_projects(session: AsyncSession, reader: ActivityReader) -> dict[str, Project]:
    """The organisation's projects whose runs this reader may see, narrowed to the `project` filter."""
    query = select(Project).where(Project.org_id == reader.org_id)
    if reader.filters.project is not None:
        query = query.where(Project.slug == reader.filters.project)
    rows = (await session.execute(query)).scalars().all()
    return {p.id: p for p in rows if reader.principal.can(Permission.PROJECT_READ, reader.org_slug, p.slug)}


def _runs_query(reader: ActivityReader, project_ids: list[str]) -> Any:
    query = (
        select(Run, WorkItem, WorkflowDef.name)
        .join(WorkItem, WorkItem.id == Run.work_item_id)
        .outerjoin(WorkflowDef, WorkflowDef.id == WorkItem.workflow_def_id)
        .where(Run.project_id.in_(project_ids))
    )
    if reader.filters.agent is not None:
        query = query.where(Run.agent_slug == reader.filters.agent)
    if reader.filters.workflow is not None:
        query = query.where(WorkflowDef.name == reader.filters.workflow)
    return query


def _sessions_query(reader: ActivityReader) -> Any:
    query = select(AgentSessionRecord).where(
        AgentSessionRecord.org_id == reader.org_id,
        AgentSessionRecord.user_id == reader.principal.user_id,
        AgentSessionRecord.kind != "conversation",  # a conversation's entries are its turns
    )
    if reader.filters.agent is not None:
        query = query.where(AgentSessionRecord.agent_slug == reader.filters.agent)
    return query


async def list_activity(session: AsyncSession, reader: ActivityReader) -> ActivityList:
    """In flight newest first, then what ended in the last hour, newest ended first (at most 50)."""
    filters = reader.filters
    gateways = _Gateways(session)
    since = utcnow() - FINISHED_WINDOW
    in_flight: list[ActivityEntry] = []
    finished: list[ActivityEntry] = []
    projects = await _readable_projects(session, reader)
    if filters.wants("run") and projects:
        runs = _runs_query(reader, list(projects))
        started = func.coalesce(Run.started_at, Run.created_at)
        live = await session.execute(
            runs.where(Run.status.in_(IN_FLIGHT)).order_by(started.desc()).limit(IN_FLIGHT_MAX)
        )
        in_flight += await _run_entries(session, reader, [tuple(r) for r in live.all()], projects, gateways)
        ended = await session.execute(
            runs.where(Run.status.not_in(IN_FLIGHT), Run.ended_at >= since)
            .order_by(Run.ended_at.desc())
            .limit(FINISHED_MAX * 4)
        )
        finished += await _run_entries(session, reader, [tuple(r) for r in ended.all()], projects, gateways)
    if filters.wants("session") and projects:
        sessions = _sessions_query(reader).where(AgentSessionRecord.project_id.in_(list(projects)))
        live_sessions = await session.execute(
            sessions.where(AgentSessionRecord.status == "open")
            .order_by(AgentSessionRecord.created_at.desc())
            .limit(IN_FLIGHT_MAX)
        )
        in_flight += await _session_entries(
            session, reader, list(live_sessions.scalars()), projects, gateways
        )
        closed = await session.execute(
            sessions.where(AgentSessionRecord.status != "open", AgentSessionRecord.closed_at >= since)
            .order_by(AgentSessionRecord.closed_at.desc())
            .limit(FINISHED_MAX)
        )
        finished += await _session_entries(session, reader, list(closed.scalars()), projects, gateways)
    if filters.wants("turn"):
        turns_live, turns_ended = await turn_entries(reader, since, gateways.of_sessions(reader.org_id))
        in_flight += turns_live
        finished += turns_ended
    in_flight.sort(key=lambda e: e.started_at, reverse=True)
    finished.sort(key=lambda e: e.ended_at or e.started_at, reverse=True)
    return ActivityList(in_flight=in_flight, finished=finished[:FINISHED_MAX])


# ───────────────────────────── the stream ─────────────────────────────


async def _project_seen(session: AsyncSession, reader: ActivityReader, project_id: str) -> Project | None:
    project = await session.get(Project, project_id)
    if project is None or project.org_id != reader.org_id:
        return None
    if not reader.principal.can(Permission.PROJECT_READ, reader.org_slug, project.slug):
        return None
    return project


async def _run_of_event(
    session: AsyncSession, reader: ActivityReader, event: ChoregosEvent
) -> tuple[str, ActivityEntry] | None:
    run = await session.get(Run, str(event.data.get("run_id") or ""))
    if run is None:
        return None
    project = await _project_seen(session, reader, run.project_id)
    item = await session.get(WorkItem, run.work_item_id)
    if project is None or item is None:
        return None
    definition = await session.get(WorkflowDef, item.workflow_def_id) if item.workflow_def_id else None
    row = (run, item, definition.name if definition else None)
    entries = await _run_entries(session, reader, [row], {project.id: project}, _Gateways(session))
    if not entries:
        return None
    entry = entries[0]
    if event.type == EventType.RUN_FINISHED:
        # The event may be read before the row it follows is committed (SQLite publishes at once): the
        # event says how the run ended.
        if entry.status in IN_FLIGHT:
            status = str(event.data.get("status") or "")
            entry = entry.model_copy(
                update={
                    "status": status or entry.status,
                    "ended_at": entry.ended_at or event.time,
                    "can_stop": False,
                }
            )
        return "activity.ended", entry
    return ("activity.upsert" if entry.status in IN_FLIGHT else "activity.ended"), entry


async def _session_of_event(
    session: AsyncSession, reader: ActivityReader, event: ChoregosEvent
) -> tuple[str, ActivityEntry] | None:
    record = await session.get(AgentSessionRecord, str(event.data.get("session_id") or ""))
    if record is None or record.org_id != reader.org_id or record.user_id != reader.principal.user_id:
        return None
    if record.kind == "conversation":
        return None  # its entries are turns (`_turn_of_event`)
    project = await _project_seen(session, reader, record.project_id)
    if project is None:
        return None
    entries = await _session_entries(session, reader, [record], {project.id: project}, _Gateways(session))
    entry = entries[0]
    return ("activity.upsert" if entry.status == "open" else "activity.ended"), entry


async def _turn_of_event(
    session: AsyncSession, reader: ActivityReader, event: ChoregosEvent
) -> tuple[str, ActivityEntry] | None:
    """The turn of the conversation an event names, if the reader may see it (read in the platform scope:
    an auditor reads other people's turns, never their content)."""
    live, ended = await turn_entries(
        reader,
        utcnow() - FINISHED_WINDOW,
        _Gateways(session).of_sessions(reader.org_id),
        only=str(event.data.get("session_id")),
    )
    if live:
        return "activity.upsert", live[0]
    if ended:
        return "activity.ended", ended[0]
    return None


async def entry_of_event(
    session: AsyncSession, reader: ActivityReader, event: ChoregosEvent
) -> tuple[str, ActivityEntry] | None:
    """The SSE event name and the entry an event of the platform changes, or `None` when it changes
    none this reader may see or wants. A run's events (`run.queued|started|progress|finished`) give
    its run; any event that names a `session_id` (a session's tool call) gives its session."""
    found: tuple[str, ActivityEntry] | None = None
    if event.type in RUN_EVENTS and event.data.get("run_id") and reader.filters.wants("run"):
        found = await _run_of_event(session, reader, event)
    elif event.data.get("session_id"):
        if reader.filters.wants("session"):
            found = await _session_of_event(session, reader, event)
        if found is None and reader.filters.wants("turn"):
            found = await _turn_of_event(session, reader, event)
    if found is None or not reader.filters.keeps(found[1]):
        return None
    return found


def as_message(event: ChoregosEvent, name: str, entry: ActivityEntry) -> dict[str, str]:
    return {"id": event.id, "event": name, "data": entry.model_dump_json()}


async def activity_messages(
    reader: ActivityReader,
    events: asyncio.Queue[ChoregosEvent],
    opened: Callable[[], Any],
) -> AsyncIterator[dict[str, str]]:
    """The stream's messages, one per event that changes an entry the reader sees. Each event is read
    in its own short session (`opened()`): a stream lives longer than a request's."""
    while True:
        event = await events.get()
        async with opened() as session:
            found = await entry_of_event(session, reader, event)
        if found is not None:
            yield as_message(event, *found)


__all__ = [
    "FINISHED_MAX",
    "FINISHED_WINDOW",
    "IN_FLIGHT",
    "SPEND_TTL_S",
    "ActivityFilters",
    "ActivityReader",
    "activity_messages",
    "entry_of_event",
    "forget_spends",
    "list_activity",
    "spend_so_far",
]
