# SPDX-License-Identifier: Apache-2.0
"""Internal event bus: persistence + SSE broadcast to the front end.

One path for everything that moves: `broadcast()` publishes an event, in the process on SQLite, by
PostgreSQL `NOTIFY` everywhere else, and every API replica, which `LISTEN`s, wakes its SSE
subscribers. Subscribers are per project, per subject or per run, with resumption by
`Last-Event-ID`.

WHY POSTGRES
------------
The bus lived in the memory of one process, and a docstring promised "in multi-replica, Postgres
LISTEN/NOTIFY takes over" without a single line doing it (inventory of 2026-09-24). Yet almost all
events are born in the ORCHESTRATOR, another process, and the API runs at two or three replicas:
the product's two live streams (provisioning, a run's log) were structurally mute. `NOTIFY` is
transactional: an event leaves only at commit, never before, which also fixes the publication
before commit that used to happen.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Iterator
from typing import Any

from choregos_contracts import ChoregosEvent, EventType
from choregos_core.compat import alias_names

from .logging import get_logger

MAX_BUFFER = 500
CHANNEL = "choregos_events"
#: How the relay's `LISTEN` connection is named in `pg_stat_activity`, and how often it is checked.
APPLICATION_NAME = "choregos-events-relay"
KEEPALIVE_S = 30.0
#: Beyond it, PostgreSQL refuses the notification (8000 bytes): `data` is lightened.
MAX_PAYLOAD_BYTES = 7500
logger = get_logger("choregos.events")


class EventBus:
    """Broadcast in the memory of one process. In multi-replica, Postgres LISTEN/NOTIFY takes over."""

    def __init__(self, buffer_size: int = MAX_BUFFER) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[ChoregosEvent]]] = defaultdict(set)
        self._buffer: dict[str, deque[ChoregosEvent]] = defaultdict(lambda: deque(maxlen=buffer_size))
        self._counter = 0

    def publish(self, event: ChoregosEvent) -> None:
        self._counter += 1
        for topic in self._topics(event):
            self._buffer[topic].append(event)
            for queue in list(self._subscribers.get(topic, ())):
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(event)

    @staticmethod
    def _topics(event: ChoregosEvent) -> list[str]:
        topics = ["*"]
        if event.project_slug:
            topics.append(f"project:{event.project_slug}")
        if event.subject:
            topics.append(f"subject:{event.subject}")
        run_id = event.data.get("run_id")
        if run_id:
            topics.append(f"run:{run_id}")
        return topics

    def replay(self, topic: str, after_id: str | None = None) -> list[ChoregosEvent]:
        """Buffered events after `after_id` (SSE resumption)."""
        events = list(self._buffer.get(topic, ()))
        if after_id is None:
            return events
        for index, event in enumerate(events):
            if event.id == after_id:
                return events[index + 1 :]
        return events

    async def subscribe(self, topic: str, after_id: str | None = None) -> AsyncIterator[ChoregosEvent]:
        """SSE subscription: replays the buffer, then follows the stream."""
        queue: asyncio.Queue[ChoregosEvent] = asyncio.Queue(maxsize=1000)
        self._subscribers[topic].add(queue)
        try:
            for event in self.replay(topic, after_id):
                yield event
            while True:
                yield await queue.get()
        finally:
            self._subscribers[topic].discard(queue)

    @contextlib.contextmanager
    def listen(self, topic: str, size: int = 100) -> Iterator[asyncio.Queue[ChoregosEvent]]:
        """A queue that receives the topic's live events, nothing replayed; a full queue drops."""
        queue: asyncio.Queue[ChoregosEvent] = asyncio.Queue(maxsize=size)
        self._subscribers[topic].add(queue)
        try:
            yield queue
        finally:
            self._subscribers[topic].discard(queue)

    def subscriber_count(self, topic: str) -> int:
        return len(self._subscribers.get(topic, ()))


_bus = EventBus()


def get_bus() -> EventBus:
    return _bus


def emit(
    type_: EventType,
    *,
    source: str = "/choregos/api",
    subject: str | None = None,
    project_slug: str | None = None,
    **data: Any,
) -> ChoregosEvent:
    """Shortcut: builds the CloudEvents event and publishes it."""
    event = ChoregosEvent.emit(type_, source=source, subject=subject, project_slug=project_slug, **data)
    _bus.publish(event)
    return event


def _payload_of(event: ChoregosEvent) -> str:
    """The event as JSON, lightened if it exceeds what a notification can carry."""
    payload = event.model_dump_json()
    if len(payload.encode()) <= MAX_PAYLOAD_BYTES:
        return payload
    lightened = event.model_copy(
        update={
            "data": {"truncated": True, **{k: v for k, v in event.data.items() if k in {"run_id", "state"}}}
        }
    )
    return lightened.model_dump_json()


async def broadcast(session: Any, event: ChoregosEvent) -> None:
    """Publishes the event: `NOTIFY` in the transaction of `session` on PostgreSQL, otherwise in memory.

    On PostgreSQL, the notification leaves AT COMMIT: the replicas (and this process) receive it
    through the relay. On SQLite (dev, tests), the process's bus is enough.
    """
    from sqlalchemy import text

    from .config import get_settings

    if get_settings().is_sqlite:
        _bus.publish(event)
        return
    await session.execute(
        text("SELECT pg_notify(:channel, :payload)"), {"channel": CHANNEL, "payload": _payload_of(event)}
    )


class PostgresRelay:
    """One `LISTEN` connection per replica, which republishes into the local bus. Reconnects on its own."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
        self._task: asyncio.Task[None] | None = None
        self.received = 0

    @property
    def recus(self) -> int:  # lang: fr-ok alias:internal
        """The former name of `received`."""
        return self.received

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    demarrer = start  # lang: fr-ok alias:internal

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    arreter = stop  # lang: fr-ok alias:internal

    def _receive(self, _conn: Any, _pid: int, _channel: str, payload: str) -> None:
        try:
            event = ChoregosEvent.model_validate_json(payload)
        except Exception as exc:
            logger.warning("unreadable notification", error=str(exc)[:120])
            return
        self.received += 1
        _bus.publish(event)

    async def _loop(self) -> None:
        import asyncpg

        wait = 1.0
        while True:
            try:
                conn = await asyncpg.connect(self.dsn, server_settings={"application_name": APPLICATION_NAME})
                # S40-23j: a connection the server closes (a failover, `pg_terminate_backend`) is
                # seen at once, not at the next keepalive: until then the replica heard nothing.
                lost = asyncio.Event()
                conn.add_termination_listener(lambda _conn, lost=lost: lost.set())
                try:
                    await conn.add_listener(CHANNEL, self._receive)
                    logger.info("PostgreSQL event relay attached", channel=CHANNEL)
                    wait = 1.0
                    while not lost.is_set():
                        with contextlib.suppress(TimeoutError):
                            await asyncio.wait_for(lost.wait(), timeout=KEEPALIVE_S)
                        if not lost.is_set():
                            await conn.execute("SELECT 1")  # a connection dead without a word
                    raise ConnectionError("the LISTEN connection was closed by the server")
                finally:
                    with contextlib.suppress(Exception):
                        await conn.close()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "PostgreSQL event relay lost, reconnecting", error=str(exc)[:120], retry_in_s=wait
                )
                await asyncio.sleep(wait)
                wait = min(wait * 2, 30.0)


def sse_format(event: ChoregosEvent) -> dict[str, str]:
    """Returns an event in the format `sse_starlette.EventSourceResponse` expects."""
    return {
        "id": event.id,
        "event": str(event.type),
        "data": event.model_dump_json(),
    }


CANAL = CHANNEL  # lang: fr-ok alias:internal
TAILLE_MAX = MAX_PAYLOAD_BYTES  # lang: fr-ok alias:internal
diffuser = broadcast  # lang: fr-ok alias:internal
_charge_utile = _payload_of  # lang: fr-ok alias:internal
RelaisPostgres = PostgresRelay  # lang: fr-ok alias:internal

alias_names(
    __name__,
    {
        "CANAL": "CHANNEL",  # lang: fr-ok alias:internal
        "TAILLE_MAX": "MAX_PAYLOAD_BYTES",  # lang: fr-ok alias:internal
        "diffuser": "broadcast",  # lang: fr-ok alias:internal
        "_charge_utile": "_payload_of",  # lang: fr-ok alias:internal
        "RelaisPostgres": "PostgresRelay",  # lang: fr-ok alias:internal
    },
)
