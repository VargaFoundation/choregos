# SPDX-License-Identifier: Apache-2.0
"""What bounds and protects a conversation's turns (S63-04b, ADR 0134): turns, retention, compression,
one turn at a time, and cancelling the one that runs.

- `max_turns`: the turn past it is refused (409) before any key or model call. The default is on the
  conversation (`max_turns`, else `DEFAULT_CONVERSATION_TURNS`); the installation's policy replaces it later.
- `retention_until`: moved to `now + retention_days` (30 when the conversation names none) at each turn.
- Compression: past `COMPRESS_AFTER_TOKENS` (estimated as characters / 4), the oldest turns leave the
  history sent to the model and are replaced by ONE system message, the summary. The summary is
  DETERMINISTIC (each summarised message cut at `LINE_CHARS`, the oldest lines dropped past
  `SUMMARY_CHARS`, with a marker saying so), not model-written: it costs nothing and cannot fail. The turns
  summarised are moved to `archived_messages`, which the person still reads and the model never sees.
- One turn at a time: a turn takes the conversation's row (`SELECT … FOR UPDATE` on PostgreSQL), checks that
  no other turn holds the lease (`turn_lease_until`), takes the lease and the next turn number, and
  commits, so a concurrent turn is refused with 409 and never takes the same number. The lease also lapses
  by itself after `TURN_KEY_TTL_S`, so a crashed process does not lock a conversation for ever.
- Cancel: a running turn is a task registered by conversation id in the process that runs it;
  `cancel_turn` cancels it there. A cancel that reaches another replica (S63-23, PostgreSQL) finds the turn
  by its lease, sets `turn_cancel_requested_at` on the conversation's row and waits for the lease to end; the
  replica that runs the turn polls that flag every `CANCEL_POLL_S` (`watch_cancel`) and cancels its task,
  which settles as a local cancel does (tool calls stay journaled, the key revoked, the spend booked).
  The turn itself also reads that flag before each model call and each tool call (`cancel_if_requested`,
  S64-09c): a stop of its assistant or of its agent (ADR 0142 §4 bis) writes it, on any database, and the
  turn ends at its next call even when no poller runs (SQLite).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any

import structlog
from choregos_core import utcnow
from choregos_core.domain import aware
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import AgentSessionRecord
from ..db.session import release_connection, session_scope
from ..errors import ApiError, conflict

log = structlog.get_logger()

#: Turns a conversation may take in all when it names no limit.
DEFAULT_CONVERSATION_TURNS = 200
#: Days a conversation is kept after its last turn when it names none.
DEFAULT_RETENTION_DAYS = 30
#: Estimated tokens of the history (system messages included) past which the oldest turns are summarised.
COMPRESS_AFTER_TOKENS = 6000
#: The latest turns compression always leaves in the history.
KEEP_TURNS = 2
LINE_CHARS = 200
SUMMARY_CHARS = 2000
SUMMARY_PREFIX = "Summary of the earlier conversation"
#: How long a turn holds the conversation: its key's own lifetime.
LEASE_S = 1800
#: How often the replica that runs a turn looks for a cancel another replica asked for.
CANCEL_POLL_S = 0.5


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """A simple estimate: characters / 4 of the text and the tool calls."""
    chars = sum(
        len(str(m.get("content") or "")) + len(json.dumps(m.get("tool_calls") or [])) for m in messages
    )
    return chars // 4


def retention_until(record: AgentSessionRecord, now: datetime) -> datetime:
    return now + timedelta(days=record.retention_days or DEFAULT_RETENTION_DAYS)


def turn_limit(record: AgentSessionRecord) -> int:
    return record.max_turns or DEFAULT_CONVERSATION_TURNS


def refuse_if_not_allowed(record: AgentSessionRecord, now: datetime | None = None) -> None:
    """The refusals a turn would meet first, from what is read (the claim re-checks them under lock)."""
    if record.turns >= turn_limit(record):
        raise ApiError(
            409,
            "Turn limit reached",
            f"the conversation took its {turn_limit(record)} turns: open a new one",
        )
    lease = aware(record.turn_lease_until)
    if lease is not None and lease > (now or utcnow()):
        raise conflict(f"a turn of conversation {record.id} is already running")


async def claim_turn(session: AsyncSession, record: AgentSessionRecord) -> int:
    """Takes the conversation's row, refuses a second turn, and returns the number of this one. The
    caller commits at once (`release_connection`): the lease must be seen by the next turn."""
    row = (
        await session.execute(
            select(AgentSessionRecord)
            .where(AgentSessionRecord.id == record.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    now = utcnow()
    refuse_if_not_allowed(row, now)
    row.turns += 1
    row.turn_lease_until = now + timedelta(seconds=LEASE_S)
    row.turn_cancel_requested_at = None  # a cancel asked of an earlier turn is not this one's
    row.retention_until = retention_until(row, now)
    return int(row.turns)


# ───────────────────────────── compression ─────────────────────────────


def _is_summary(message: dict[str, Any]) -> bool:
    return message.get("role") == "system" and str(message.get("content") or "").startswith(SUMMARY_PREFIX)


def _line(message: dict[str, Any]) -> str:
    text = " ".join(str(message.get("content") or "").split())
    cut = text if len(text) <= LINE_CHARS else text[:LINE_CHARS] + "..."
    return f"{message['role']}: {cut}"


def compress_history(record: AgentSessionRecord, incoming: str) -> int:
    """Moves the oldest turns out of `record.messages` into one summary message while the estimate
    (history and the incoming message) is over the threshold. Returns the number of turns summarised."""
    messages = list(record.messages or [])
    head = [m for m in messages if m.get("role") == "system" and not _is_summary(m)]
    summaries = [m for m in messages if _is_summary(m)]
    body = [m for m in messages if m.get("role") != "system"]
    turns: list[list[dict[str, Any]]] = []
    for message in body:
        if message.get("role") == "user" or not turns:
            turns.append([])
        turns[-1].append(message)
    total = estimate_tokens(messages) + len(incoming) // 4
    taken: list[list[dict[str, Any]]] = []
    while len(turns) > KEEP_TURNS and total > COMPRESS_AFTER_TOKENS:
        oldest = turns.pop(0)
        taken.append(oldest)
        total -= estimate_tokens(oldest)
    if not taken:
        return 0
    lines = [str(m["content"]).split("\n", 1)[1] for m in summaries if "\n" in str(m["content"])]
    lines = "\n".join(lines).split("\n") if lines else []
    for turn in taken:
        lines.extend(_line(m) for m in turn if m.get("role") in ("user", "assistant") and m.get("content"))
    marker = ""
    while len("\n".join(lines)) > SUMMARY_CHARS and len(lines) > 1:
        lines.pop(0)
        marker = " [older lines dropped]"
    summary = {
        "role": "system",
        "content": f"{SUMMARY_PREFIX} (cut, not model-written){marker}:\n" + "\n".join(lines),
    }
    record.archived_messages = [*(record.archived_messages or []), *(m for turn in taken for m in turn)]
    record.messages = [*head, summary, *(m for turn in turns for m in turn)]
    return len(taken)


# ───────────────────────────── cancelling ─────────────────────────────

_RUNNING: dict[str, asyncio.Task[Any]] = {}


def register_turn(conversation_id: str, task: asyncio.Task[Any]) -> None:
    _RUNNING[conversation_id] = task


def forget_turn(conversation_id: str, task: asyncio.Task[Any]) -> None:
    if _RUNNING.get(conversation_id) is task:
        del _RUNNING[conversation_id]


async def cancel_turn(
    conversation_id: str, session: AsyncSession | None = None, *, wait_s: float = 30.0
) -> bool:
    """Cancels the turn of `conversation_id` and waits until it settled (its spend booked, its key revoked).
    In this process when it runs here; otherwise, given a `session` on PostgreSQL, through the flag the
    running replica polls. False when no turn runs."""
    task = _RUNNING.get(conversation_id)
    if task is not None and not task.done():
        task.cancel()
        await asyncio.wait({task}, timeout=wait_s)
        return True
    if session is None or get_settings().is_sqlite:
        return False
    return await _cancel_elsewhere(session, conversation_id, wait_s)


async def _cancel_elsewhere(session: AsyncSession, conversation_id: str, wait_s: float) -> bool:
    asked = await session.execute(
        update(AgentSessionRecord)
        .where(AgentSessionRecord.id == conversation_id, AgentSessionRecord.turn_lease_until > utcnow())
        .values(turn_cancel_requested_at=utcnow())
    )
    if not getattr(asked, "rowcount", 0):
        return False  # no turn holds the conversation
    await release_connection(session)  # committed: the replica that runs the turn must see it
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_s
    while loop.time() < deadline:
        await asyncio.sleep(CANCEL_POLL_S / 2)
        lease = (
            await session.execute(
                select(AgentSessionRecord.turn_lease_until).where(AgentSessionRecord.id == conversation_id)
            )
        ).scalar_one_or_none()
        await release_connection(session)
        if lease is None or (aware(lease) or utcnow()) <= utcnow():
            break
    return True


async def watch_cancel(conversation_id: str, task: asyncio.Task[Any]) -> None:
    """Runs beside a turn on PostgreSQL: cancels `task` once another replica asked for it."""
    while not task.done():
        await asyncio.sleep(CANCEL_POLL_S)
        try:
            async with session_scope(orgs="*") as session:
                asked = (
                    await session.execute(
                        select(AgentSessionRecord.turn_cancel_requested_at).where(
                            AgentSessionRecord.id == conversation_id
                        )
                    )
                ).scalar_one_or_none()
        except Exception as failure:  # a failing poll is tried again, the turn goes on
            log.warning("conversation.cancel_poll_failed", error=type(failure).__name__)
            continue
        if asked is not None and not task.done():
            task.cancel()
            return


async def cancel_if_requested(session: AsyncSession, record: AgentSessionRecord) -> None:
    """Between a turn's calls: raises `CancelledError` when a cancel was asked on the conversation's row
    (another replica, a stop of its assistant or its agent), so the turn settles as a cancelled one. A
    session that is not a conversation has no turn to cancel."""
    if record.kind != "conversation":
        return
    asked = (
        await session.execute(
            select(AgentSessionRecord.turn_cancel_requested_at).where(AgentSessionRecord.id == record.id)
        )
    ).scalar_one_or_none()
    if asked is not None:
        log.info("conversation.turn_cancel_read", session_id=record.id)
        raise asyncio.CancelledError("the turn's cancel was requested")


async def clear_cancel_request(session: AsyncSession, conversation_id: str) -> None:
    """The turn ended: its flag goes with its lease (an explicit UPDATE, the row in memory never saw it)."""
    await session.execute(
        update(AgentSessionRecord)
        .where(AgentSessionRecord.id == conversation_id)
        .values(turn_cancel_requested_at=None)
    )
