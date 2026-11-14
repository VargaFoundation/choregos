# SPDX-License-Identifier: Apache-2.0
"""Conversation turns in the list of what is in flight (S64-09c, ADR 0142 §1).

A conversation (an `agent_sessions` row of kind `conversation`) has a turn in flight while its lease
(`turn_lease_until`) is live; the turn's entry then reads `running`. Its last turn ended in the last hour
reads how it ended: `cancelled` when the journal says `turn.cancelled`, `failed` when its response failed,
`completed` otherwise. One entry per conversation, its id the conversation's: what its person cancels
(`POST /me/conversations/{id}/cancel`).

Who sees a turn: the conversation's person, and whoever holds `assistant:audit` on its assistant
(`assistant_access.holds`). Conversations live in their people's personal spaces, which an auditor cannot
read under row-level security (ADR 0134): they are read in the platform scope, narrowed in SQL to the
organisation, to the reader's own and to the installations the reader audits. Nothing of their content is
read: the entry carries ids, counts, the model and the spend. `can_stop` is the person's alone.

A turn's spend so far is the gateway's count for its key (`turn:<conversation>:<n>`), asked at most every
30 s (`activity.spend_so_far`); once ended, the ledger's line booked for it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from choregos_core import utcnow
from choregos_core.domain import aware
from sqlalchemy import and_, func, or_, select

from ..db.models import (
    AgentSessionEvent,
    AgentSessionRecord,
    AssistantInstallation,
    CostLedger,
    GatewayKeyRow,
    User,
)
from ..db.session import session_scope
from ..rbac import Permission
from ..schemas.activity import ActivityEntry, SpendSoFar
from .assistant_access import holds
from .initiator import initiator

if TYPE_CHECKING:
    from choregos_adapters.base import GatewayAdapter
    from sqlalchemy.ext.asyncio import AsyncSession

    from .activity import ActivityReader

#: The journal's types that say how a turn went.
STARTED, ENDED, CANCELLED, FAILED = "turn.started", "turn.ended", "turn.cancelled", "response.failed"


async def _audited(session: AsyncSession, reader: ActivityReader) -> list[str]:
    """The organisation's installations whose turns the reader may see without being their person."""
    rows = await session.execute(
        select(AssistantInstallation).where(AssistantInstallation.org_id == reader.org_id)
    )
    return [
        row.id
        for row in rows.scalars()
        if holds(reader.principal, Permission.ASSISTANT_AUDIT, reader.org_slug, row)
    ]


def _visible(reader: ActivityReader, audited: list[str]) -> Any:
    query = select(AgentSessionRecord).where(
        AgentSessionRecord.org_id == reader.org_id,
        AgentSessionRecord.kind == "conversation",
        or_(
            AgentSessionRecord.user_id == reader.principal.user_id,
            AgentSessionRecord.installation_id.in_(audited),
        ),
    )
    if reader.filters.agent is not None:
        query = query.where(AgentSessionRecord.agent_slug == reader.filters.agent)
    if reader.filters.assistant is not None:
        query = query.where(AgentSessionRecord.installation_id == reader.filters.assistant)
    return query


async def _last_turns(session: AsyncSession, ids: list[str]) -> dict[str, list[AgentSessionEvent]]:
    """Each conversation's events since its last `turn.started`, in order: its last turn's journal."""
    if not ids:
        return {}
    started = (
        select(AgentSessionEvent.session_id.label("owner"), func.max(AgentSessionEvent.seq).label("seq"))
        .where(AgentSessionEvent.session_id.in_(ids), AgentSessionEvent.type == STARTED)
        .group_by(AgentSessionEvent.session_id)
        .subquery()
    )
    rows = await session.execute(
        select(AgentSessionEvent)
        .join(
            started,
            and_(AgentSessionEvent.session_id == started.c.owner, AgentSessionEvent.seq >= started.c.seq),
        )
        .order_by(AgentSessionEvent.session_id, AgentSessionEvent.seq)
    )
    found: dict[str, list[AgentSessionEvent]] = {}
    for event in rows.scalars():
        found.setdefault(event.session_id, []).append(event)
    return found


def _how_it_ended(events: list[AgentSessionEvent]) -> tuple[str, datetime | None]:
    types = [e.type for e in events]
    ended = next((e for e in reversed(events) if e.type == ENDED), None)
    when = aware(ended.ts) if ended is not None else None
    if CANCELLED in types:
        return "cancelled", when
    if FAILED in types:
        return "failed", when
    return "completed", when


async def _emails(session: AsyncSession, user_ids: set[str]) -> dict[str, str]:
    if not user_ids:
        return {}
    rows = await session.execute(select(User.id, User.email).where(User.id.in_(user_ids)))
    return {str(uid): str(email) for uid, email in rows.all()}


async def _booked(session: AsyncSession, conversation_id: str, since: datetime | None) -> SpendSoFar | None:
    """The ledger's line of the conversation's last turn, booked when it ended."""
    query = select(CostLedger).where(CostLedger.session_id == conversation_id)
    if since is not None:
        query = query.where(CostLedger.ts >= since)
    line = (await session.execute(query.order_by(CostLedger.ts.desc()).limit(1))).scalar_one_or_none()
    if line is None:
        return None
    return SpendSoFar(
        tokens_in=line.tokens_in,
        tokens_out=line.tokens_out,
        tokens_cached=line.tokens_cached,
        cost_usd=line.cost_usd,
        read_at=aware(line.ts) or utcnow(),
        booked=True,
    )


async def _key_of(session: AsyncSession, record: AgentSessionRecord) -> str | None:
    run_id = f"turn:{record.id}:{record.turns}"
    return (
        await session.execute(select(GatewayKeyRow.key_id).where(GatewayKeyRow.run_id == run_id))
    ).scalar_one_or_none()


async def turn_entries(
    reader: ActivityReader,
    since: datetime,
    gateway_of: Callable[[], Awaitable[GatewayAdapter]],
    *,
    only: str | None = None,
) -> tuple[list[ActivityEntry], list[ActivityEntry]]:
    """The turns in flight, and the turns ended since `since`, that the reader may see (`only`: of this
    conversation alone, for the stream)."""
    from .activity import FINISHED_MAX, IN_FLIGHT_MAX, spend_so_far

    now = utcnow()
    async with session_scope(orgs="*") as platform:  # every person's space, narrowed below in SQL
        query = _visible(reader, await _audited(platform, reader))
        if only is not None:
            query = query.where(AgentSessionRecord.id == only)
        live = list(
            (
                await platform.execute(
                    query.where(AgentSessionRecord.turn_lease_until > now)
                    .order_by(AgentSessionRecord.turn_lease_until.desc())
                    .limit(IN_FLIGHT_MAX)
                )
            ).scalars()
        )
        live_ids = {r.id for r in live}
        recent = [
            r
            for r in (
                await platform.execute(
                    query.where(AgentSessionRecord.last_activity_at >= since)
                    .order_by(AgentSessionRecord.last_activity_at.desc())
                    .limit(FINISHED_MAX + len(live_ids))
                )
            ).scalars()
            if r.id not in live_ids
        ]
        journals = await _last_turns(platform, [r.id for r in [*live, *recent]])
        emails = await _emails(platform, {r.user_id for r in [*live, *recent]})
        in_flight, finished = [], []
        for record in live:
            events = journals.get(record.id, [])
            spend = await spend_so_far(
                f"turn:{record.id}:{record.turns}", await _key_of(platform, record), gateway_of
            )
            in_flight.append(_entry(reader, record, events, emails, "running", None, spend))
        for record in recent:
            events = journals.get(record.id, [])
            if not events:
                continue  # opened, never spoken in
            status, ended_at = _how_it_ended(events)
            started_at = aware(events[0].ts)
            spend = await _booked(platform, record.id, started_at)
            ended = ended_at or aware(record.last_activity_at)
            finished.append(_entry(reader, record, events, emails, status, ended, spend))
    return in_flight, finished


def _entry(
    reader: ActivityReader,
    record: AgentSessionRecord,
    events: list[AgentSessionEvent],
    emails: dict[str, str],
    status: str,
    ended_at: datetime | None,
    spend: SpendSoFar | None,
) -> ActivityEntry:
    own = record.user_id == reader.principal.user_id
    email = emails.get(record.user_id)
    return ActivityEntry(
        kind="turn",
        id=record.id,
        status=status,
        agent_slug=record.agent_slug,
        assistant=record.installation_id,
        model=record.model or None,
        initiated_by=initiator("person", email) if email else None,
        started_at=aware(events[0].ts)
        if events
        else (aware(record.last_activity_at or record.created_at) or utcnow()),
        ended_at=ended_at,
        last_event=events[-1].type if events else None,
        steps=len(events),
        spend=spend,
        can_stop=status == "running" and own,
    )


__all__ = ["turn_entries"]
