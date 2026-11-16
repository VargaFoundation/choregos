# SPDX-License-Identifier: Apache-2.0
"""A turn many clients can follow, and that outlives the one that sent it (S67-08, ADR 0154 §4, G2).

The turn runs as a task of its own, in a session of its own. Everything the sending request would stream
is published to a `TurnRun` first: `response.created`, one `response.output_text.delta` per chunk, then
`response.completed`, `response.failed` or `error`. Each event has a position (its SSE `id`, from 1), so
the sending request and any number of followers read the same list, and a follower that attaches late
replays what it missed, then waits for the rest.

- `Last-Event-ID` (or `after_seq`) is the position of the last event a client got: the stream resumes
  after it, so no delta is repeated and none is skipped.
- A client that leaves does not stop a `detach`ed turn: the task is not tied to the request, so the turn
  settles, books its spend and revokes its key exactly once, as it does when nobody ever left (ADR 0008:
  the booking lives in the turn, not in whoever watches it). Without `detach`, the sending client leaving
  still cancels the turn, as before.
- The positions live in this process, for the latest turn of each of the last `KEPT_RUNS` conversations. The
  delta text is not journaled (the journal keeps facts, not chunks), so a turn that runs on another replica,
  or one this process no longer remembers, cannot be replayed delta by delta from here.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from uuid import uuid4

from ..db.session import name_the_user, session_scope
from ..errors import ApiError
from ..rbac import Principal
from ..schemas.agent_sessions import AgentDelta, AgentResponse

#: Conversations whose latest turn is remembered (the oldest finished one is forgotten first).
KEPT_RUNS = 256

CREATED = "response.created"
DELTA = "response.output_text.delta"


class TurnRun:
    """The events of one turn, in order, and whoever waits for the next."""

    def __init__(self, conversation_id: str) -> None:
        self.conversation_id = conversation_id
        self.response_id = f"resp_{uuid4().hex}"
        self.events: list[tuple[str, str]] = []
        self.finished = False
        self.task: asyncio.Task[None] | None = None
        self._changed = asyncio.Condition()

    async def publish(self, name: str, data: str, *, last: bool = False) -> None:
        async with self._changed:
            self.events.append((name, data))
            self.finished = self.finished or last
            self._changed.notify_all()

    def _has_more(self, position: int) -> Callable[[], bool]:
        return lambda: len(self.events) > position or self.finished

    async def follow(self, after: int = 0) -> AsyncIterator[dict[str, str]]:
        """The events after position `after`, then the ones that come, until the turn's last."""
        position = max(after, 0)
        while True:
            async with self._changed:
                await self._changed.wait_for(self._has_more(position))
                ready = self.events[position:]
                done = self.finished
            for name, data in ready:
                position += 1
                yield {"id": str(position), "event": name, "data": data}
            if done and position >= len(self.events):
                return


_RUNS: dict[str, TurnRun] = {}


def run_of(conversation_id: str) -> TurnRun | None:
    return _RUNS.get(conversation_id)


def is_running(conversation_id: str) -> bool:
    run = _RUNS.get(conversation_id)
    return run is not None and not run.finished


def _remember(run: TurnRun) -> None:
    _RUNS[run.conversation_id] = run
    while len(_RUNS) > KEPT_RUNS:
        oldest = next((k for k, v in _RUNS.items() if v.finished and v is not run), None)
        if oldest is None:
            break
        del _RUNS[oldest]


def position_after(last_event_id: str | None, after_seq: int | None) -> int:
    """`Last-Event-ID` wins over `after_seq`; anything that is not a position counts as none."""
    for raw in (last_event_id, after_seq):
        try:
            if raw is not None and int(raw) >= 0:
                return int(raw)
        except ValueError:
            continue
    return 0


def start_turn(principal: Principal, conversation_id: str, text: str) -> TurnRun:
    """Starts the turn as a task of its own and returns the run to read it from."""
    from ..routers.conversations import mine  # the one place that says whose a conversation is
    from .assistant_conversations import require_audience
    from .conversation_signals import with_signals
    from .conversations import converse

    run = TurnRun(conversation_id)
    _remember(run)

    async def on_delta(chunk: str) -> None:
        await run.publish(DELTA, AgentDelta(response_id=run.response_id, delta=chunk).model_dump_json())

    async def go() -> None:
        await run.publish(
            CREATED,
            AgentResponse(
                id=run.response_id, session_id=conversation_id, status="completed"
            ).model_dump_json(),
        )
        try:
            async with session_scope(orgs=list(principal.org_roles)) as session:
                await name_the_user(session, principal.user_id)
                record = await mine(session, principal, conversation_id)
                await require_audience(session, principal, record)
                answer = await with_signals(session, await converse(session, record, text, on_delta=on_delta))
            await run.publish(f"response.{answer.status}", answer.model_dump_json(), last=True)
        except ApiError as refused:
            await run.publish(
                "error", json.dumps({"status": refused.status_code, "detail": refused.detail}), last=True
            )
        except asyncio.CancelledError:
            # A cancelled turn settles `failed` / `cancelled` in `converse`; this is the task itself ending.
            if not run.finished:
                await run.publish("error", json.dumps({"status": 499, "detail": "cancelled"}), last=True)
            raise
        except BaseException:
            if not run.finished:
                await run.publish(
                    "error", json.dumps({"status": 500, "detail": "the turn failed"}), last=True
                )
            raise

    run.task = asyncio.ensure_future(go())
    return run
