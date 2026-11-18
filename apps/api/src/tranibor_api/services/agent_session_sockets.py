# SPDX-License-Identifier: Apache-2.0
"""A person on a hosted agent's own socket (S41-21, ADR 0080 §10): `GET …/agents/{slug}/realtime`
relays the caller's text frames to the container's `/invocations_ws`, in the session's pod.

The API never connects to the pod. Each frame the caller sends is journaled (`socket.in`); the pod's
runner reads them (`GET /internal/agent-sessions/{id}/socket/next`) and writes them to the
container's socket. What the container sends back, the runner numbers from 1 and posts
(`POST …/socket/out`): each number is journaled once (`socket.out`), so a post sent again changes
nothing. The caller's handler reads the journal and sends each frame once, in order, unmodified.

So the session outlives its caller's socket, on whichever API replica the caller comes back to. A
socket that drops without a close frame (1006) is journaled `socket.detached`; a caller that comes
back within `agent_realtime.RESUME_GRACE_S` with the session's id and `after` (how many of the
container's frames it read) gets the rest, then the live ones. A newer socket on the same session
takes over (`socket.attached`): the older one is closed. Past the grace, or when the caller closes
its socket, the session closes and its pod stops; when the container closes its own, the caller's
is closed with its code.

A new socket's first frame is the platform's, `{"type": "choregos.session", "session_id": …}`: the
id to come back with. It is not one of the container's frames and `after` does not count it.

Text frames only, of at most `SOCKET_FRAME_BYTES`: the journal keeps the frames as text, and a
binary frame (audio) is refused with 1003.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import WebSocket
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AgentSessionEvent, AgentSessionRecord
from ..db.session import session_scope
from ..errors import ApiError
from ..logging import get_logger
from ..schemas.agent_sessions import (
    SOCKET_FRAME_BYTES,
    AgentSessionSocketFrame,
    AgentSessionSocketNext,
    AgentSessionSocketOut,
)
from . import agent_realtime
from .agent_hosted_sessions import POLL_S, ask_for_pod
from .agent_realtime import DROPPED, Ending

log = get_logger(__name__)

#: How often an attached caller tells the pod its session is alive, in seconds.
TOUCH_S = 60.0
#: A journal write that lost the race for its `seq` to another writer (the pod, another socket) is
#: tried again this many times.
_ATTEMPTS = 8
#: The caller's events the pod reads, by journal type.
_FRAMES = {"socket.open": "open", "socket.in": "in", "socket.close": "close"}
#: Close codes a server may not send: the container's are replaced by 1011.
_UNSENDABLE = frozenset({1004, 1005, 1006, 1015})
_REASON = 120

Act = Callable[[AsyncSession, AgentSessionRecord], Awaitable[Any]]


async def write(session_id: str, act: Act, *, orgs: list[str] | str) -> Any:
    """`act` in a short transaction of its own, tried again when another writer took the journal's
    next `seq` first; `None` when the session does not exist."""
    for attempt in range(_ATTEMPTS):
        try:
            async with session_scope(orgs=orgs) as db:
                record = await db.get(AgentSessionRecord, session_id)
                if record is None:
                    return None
                return await act(db, record)
        except (IntegrityError, OperationalError):
            if attempt == _ATTEMPTS - 1:
                raise
            await asyncio.sleep(0.02 * (attempt + 1))
    return None  # pragma: no cover - the loop returns or raises


async def _note(session_id: str, org_slug: str, type_: str, payload: dict[str, Any]) -> int:
    """Journals one event; its `seq`."""
    from .agent_sessions import journal

    async def act(db: AsyncSession, record: AgentSessionRecord) -> int:
        await journal(db, record, type_, payload)
        return int(await _last_seq(db, session_id))

    return int(await write(session_id, act, orgs=[org_slug]) or 0)


async def _last_seq(db: AsyncSession, session_id: str) -> int:
    found = await db.execute(
        select(func.coalesce(func.max(AgentSessionEvent.seq), 0)).where(
            AgentSessionEvent.session_id == session_id
        )
    )
    return int(found.scalar_one())


async def _events(
    db: AsyncSession, session_id: str, after: int, *types: str, upto: int | None = None
) -> list[AgentSessionEvent]:
    query = select(AgentSessionEvent).where(
        AgentSessionEvent.session_id == session_id,
        AgentSessionEvent.seq > after,
        AgentSessionEvent.type.in_(types),
    )
    if upto is not None:
        query = query.where(AgentSessionEvent.seq <= upto)
    rows = await db.execute(query.order_by(AgentSessionEvent.seq))
    return list(rows.scalars())


async def last_out(db: AsyncSession, session_id: str) -> int:
    """The number of the last of the container's frames the journal holds (0 before the first)."""
    rows = await _events(db, session_id, 0, "socket.out")
    return int((rows[-1].payload or {}).get("n", 0)) if rows else 0


# ───────────────────────────── the pod's side ─────────────────────────────


async def socket_frames(db: AsyncSession, record: AgentSessionRecord, after: int) -> AgentSessionSocketNext:
    """The caller's frames journaled after `after`, for the pod.

    The cursor is read first and the frames up to it: read after them, it could pass a frame
    committed in between (PostgreSQL reads each statement's own snapshot), which the pod would then
    never be sent. The journal's numbers commit in order, so nothing at or below it comes later."""
    cursor = max(after, await _last_seq(db, record.id))
    rows = await _events(db, record.id, after, *_FRAMES, upto=cursor)
    frames = [
        AgentSessionSocketFrame(
            seq=row.seq,
            kind=_FRAMES[row.type],
            text=str((row.payload or {}).get("text") or ""),
            code=(row.payload or {}).get("code"),
        )
        for row in rows
    ]
    return AgentSessionSocketNext(frames=frames, cursor=cursor, out_n=await last_out(db, record.id))


async def record_out(db: AsyncSession, record: AgentSessionRecord, body: AgentSessionSocketOut) -> int:
    """The container's frames, each number journaled once, then its close; the last number held.
    A frame that would leave a hole in the numbering is refused (409): the caller counts frames."""
    from .agent_sessions import journal

    out_n = await last_out(db, record.id)
    for frame in sorted(body.frames, key=lambda f: f.n):
        if frame.n <= out_n:
            continue  # posted again: already journaled
        if frame.n != out_n + 1:
            raise ApiError(409, "Frame out of order", f"frame {frame.n} follows frame {out_n}")
        await journal(db, record, "socket.out", {"n": frame.n, "text": frame.text})
        out_n = frame.n
    for event in body.events:
        await journal(db, record, f"pod.{event.type}", dict(event.payload))
    if body.closed is not None and not await _events(db, record.id, 0, "socket.closed"):
        await journal(db, record, "socket.closed", body.closed.model_dump())
    return out_n


# ───────────────────────────── the caller's side ─────────────────────────────


async def serve_socket(
    websocket: WebSocket, session_id: str, org_slug: str, *, after: int, first: bool
) -> None:
    """Serves the caller's socket on the session until it ends; on `first`, asks for the pod."""

    async def held(db: AsyncSession, record: AgentSessionRecord) -> tuple[str, str, int]:
        return record.status, record.reason or "", await last_out(db, session_id)

    state = await write(session_id, held, orgs=[org_slug])
    if state is None or state[0] != "open":
        reason = state[1] if state else "unknown"
        await websocket.close(code=1008, reason=f"the session ended: {reason}"[:_REASON])
        return
    if after > state[2]:
        await websocket.close(code=1008, reason=f"the session sent {state[2]} frames, not {after}"[:_REASON])
        return
    await websocket.accept()
    attached = await _note(session_id, org_slug, "socket.attached", {"after": after})
    if first:
        # The platform's frame, before the container's: not counted in `after`.
        hello = {"type": agent_realtime.SESSION_EVENT, "session_id": session_id}
        await websocket.send_text(json.dumps(hello))
    if first and not await _start_pod(session_id, org_slug):
        await _end(session_id, org_slug, "hosted_unavailable")
        await websocket.close(code=1011, reason="the session's pod could not start")
        return
    reader = asyncio.create_task(_from_caller(websocket, session_id, org_slug))
    writer = asyncio.create_task(_to_caller(websocket, session_id, org_slug, attached, after))
    done, _ = await asyncio.wait({reader, writer}, return_when=asyncio.FIRST_COMPLETED)
    for task in (reader, writer):
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
    if writer in done:
        ending = writer.result()
        if ending.code:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await websocket.close(code=ending.code, reason=ending.message[:_REASON])
        return
    ending = reader.result()
    if ending.reason == "dropped":
        await _note(session_id, org_slug, "socket.detached", {"attached": attached})
        await _wait_for_caller(session_id, org_slug, attached)
        return
    if ending.reason == "closed":
        await _note(session_id, org_slug, "socket.close", {"code": ending.code})
    else:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await websocket.close(code=ending.code, reason=ending.message[:_REASON])
    await _end(session_id, org_slug, ending.reason)


async def _start_pod(session_id: str, org_slug: str) -> bool:
    async def requested(db: AsyncSession, record: AgentSessionRecord) -> Any:
        from .agent_sessions import journal

        if record.pod is None:
            record.pod = {"state": "requested"}
        await journal(db, record, "socket.open", {})
        return record.expires_at

    expires_at = await write(session_id, requested, orgs=[org_slug])
    return expires_at is not None and await ask_for_pod(session_id, expires_at, org_slug, first=True)


async def _end(session_id: str, org_slug: str, reason: str) -> None:
    from .agent_sessions import close_session

    async def act(db: AsyncSession, record: AgentSessionRecord) -> None:
        await close_session(db, record, reason=reason)

    await write(session_id, act, orgs=[org_slug])
    log.info("agent_session_socket.ended", session_id=session_id, reason=reason)


async def _from_caller(websocket: WebSocket, session_id: str, org_slug: str) -> Ending:
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            code = int(message.get("code") or 1000)
            return Ending("dropped", DROPPED) if code == DROPPED else Ending("closed", code)
        text = message.get("text")
        if text is None:
            return Ending("frame_refused", 1003, "a hosted session's socket relays text frames only")
        if len(text.encode("utf-8")) > SOCKET_FRAME_BYTES:
            return Ending("frame_too_large", 1009, f"a frame is limited to {SOCKET_FRAME_BYTES} bytes")
        await _note(session_id, org_slug, "socket.in", {"text": text})


async def _to_caller(
    websocket: WebSocket, session_id: str, org_slug: str, attached: int, after: int
) -> Ending:
    """The container's frames after `after`, then the live ones, until the session ends, the
    container closes its socket, or a newer socket takes this one's place."""
    cursor, sent, touched = 0, after, time.monotonic()
    while True:
        status, reason, events = await _watch(session_id, org_slug, cursor)
        for event in events:
            cursor = event.seq
            payload = event.payload or {}
            if event.type == "socket.out" and int(payload.get("n", 0)) > sent:
                await websocket.send_text(str(payload.get("text") or ""))
                sent = int(payload["n"])
            elif event.type == "socket.attached" and event.seq > attached:
                return Ending("replaced", 1000, "the session was resumed on another socket")
            elif event.type == "socket.closed":
                return await _agent_closed(session_id, org_slug, payload)
        if status != "open":
            return Ending(
                reason,
                1000 if reason in ("closed", "expired", "idle") else 1008,
                f"the session ended: {reason}",
            )
        if time.monotonic() - touched >= TOUCH_S:
            touched = time.monotonic()
            await ask_for_pod(session_id, None, org_slug, first=False)
        await asyncio.sleep(POLL_S)


async def _watch(session_id: str, org_slug: str, cursor: int) -> tuple[str, str, list[AgentSessionEvent]]:
    async def act(db: AsyncSession, record: AgentSessionRecord) -> tuple[str, str, list[AgentSessionEvent]]:
        events = await _events(db, session_id, cursor, "socket.out", "socket.attached", "socket.closed")
        return record.status, record.reason or "", events

    found = await write(session_id, act, orgs=[org_slug])
    return found if found is not None else ("failed", "unknown", [])


async def _agent_closed(session_id: str, org_slug: str, payload: dict[str, Any]) -> Ending:
    """The container closed its socket: the caller's is closed with its code, the session with it."""
    code = int(payload.get("code") or 1000)
    reason = str(payload.get("reason") or "")
    budget = reason == "budget_exceeded"
    await _end(session_id, org_slug, "budget_exceeded" if budget else "agent_closed")
    if code in _UNSENDABLE or not (1000 <= code <= 1014 or 3000 <= code <= 4999):
        code = 1011
    return Ending("agent_closed", code, reason or "the agent closed the session")


async def _wait_for_caller(session_id: str, org_slug: str, attached: int) -> None:
    """After a drop: the grace for a newer socket; past it, the session closes, `caller_gone`."""
    deadline = time.monotonic() + agent_realtime.RESUME_GRACE_S
    cursor = attached
    while time.monotonic() < deadline:
        status, _, events = await _watch(session_id, org_slug, cursor)
        if status != "open":
            return
        for event in events:
            cursor = event.seq
            if event.type == "socket.attached":
                return  # the caller came back: its handler serves the session now
            if event.type == "socket.closed":
                await _agent_closed(session_id, org_slug, event.payload or {})
                return
        await asyncio.sleep(POLL_S)
    await _end(session_id, org_slug, "caller_gone")


__all__ = ["SOCKET_FRAME_BYTES", "last_out", "record_out", "serve_socket", "socket_frames", "write"]
