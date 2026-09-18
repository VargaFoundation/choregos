"""Bus d'événements interne : persistance + diffusion SSE au front.

Un seul chemin pour tout ce qui bouge : `publish()` écrit dans `events` et réveille
les abonnés SSE. Les abonnés sont par projet, avec reprise par `Last-Event-ID`.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from typing import Any

from choregos_contracts import ChoregosEvent, EventType

MAX_BUFFER = 500


class EventBus:
    """Diffusion en mémoire d'un processus. En multi-réplica, Postgres LISTEN/NOTIFY prend le relais."""

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
        """Événements bufferisés après `after_id` (reprise SSE)."""
        events = list(self._buffer.get(topic, ()))
        if after_id is None:
            return events
        for index, event in enumerate(events):
            if event.id == after_id:
                return events[index + 1 :]
        return events

    async def subscribe(self, topic: str, after_id: str | None = None) -> AsyncIterator[ChoregosEvent]:
        """Abonnement SSE : rejoue le buffer puis suit le flux."""
        queue: asyncio.Queue[ChoregosEvent] = asyncio.Queue(maxsize=1000)
        self._subscribers[topic].add(queue)
        try:
            for event in self.replay(topic, after_id):
                yield event
            while True:
                yield await queue.get()
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
    """Raccourci : construit l'événement CloudEvents et le publie."""
    event = ChoregosEvent.emit(type_, source=source, subject=subject, project_slug=project_slug, **data)
    _bus.publish(event)
    return event


def sse_format(event: ChoregosEvent) -> dict[str, str]:
    """Rend un événement au format attendu par `sse_starlette.EventSourceResponse`."""
    return {
        "id": event.id,
        "event": str(event.type),
        "data": event.model_dump_json(),
    }
