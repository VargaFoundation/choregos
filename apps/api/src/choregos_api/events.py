"""Bus d'événements interne : persistance + diffusion SSE au front.

Un seul chemin pour tout ce qui bouge : `diffuser()` publie un événement — dans le
processus sur SQLite, par `NOTIFY` PostgreSQL partout ailleurs — et chaque réplique de
l'API, qui `LISTEN`, réveille ses abonnés SSE. Les abonnés sont par projet, par sujet ou
par run, avec reprise par `Last-Event-ID`.

POURQUOI POSTGRES
-----------------
Le bus était en mémoire d'un processus, et une docstring promettait « en multi-réplica,
Postgres LISTEN/NOTIFY prend le relais » sans qu'une ligne ne le fasse (état des lieux du
2026-09-24). Or presque tous les événements naissent dans l'ORCHESTRATEUR — un autre
processus — et l'API tourne à deux ou trois répliques : les deux flux en direct du produit
(provisioning, journal d'un run) étaient structurellement muets. `NOTIFY` est transactionnel :
un événement ne part qu'au commit, jamais avant — ce qui corrige aussi la publication avant
commit qu'on faisait.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from typing import Any

from choregos_contracts import ChoregosEvent, EventType

from .logging import get_logger

MAX_BUFFER = 500
CANAL = "choregos_events"
#: Au-delà, PostgreSQL refuse la notification (8000 octets) : on allège `data`.
TAILLE_MAX = 7500
logger = get_logger("choregos.events")


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


def _charge_utile(event: ChoregosEvent) -> str:
    """L'événement en JSON, allégé s'il dépasse ce qu'une notification peut porter."""
    charge = event.model_dump_json()
    if len(charge.encode()) <= TAILLE_MAX:
        return charge
    allege = event.model_copy(
        update={
            "data": {"truncated": True, **{k: v for k, v in event.data.items() if k in {"run_id", "state"}}}
        }
    )
    return allege.model_dump_json()


async def diffuser(session: Any, event: ChoregosEvent) -> None:
    """Publie l'événement : `NOTIFY` dans la transaction de `session` sur PostgreSQL, sinon en mémoire.

    Sur PostgreSQL, la notification part AU COMMIT : les répliques (et ce processus) la
    reçoivent par le relais. Sur SQLite (dev, tests), le bus du processus suffit.
    """
    from sqlalchemy import text

    from .config import get_settings

    if get_settings().is_sqlite:
        _bus.publish(event)
        return
    await session.execute(
        text("SELECT pg_notify(:canal, :charge)"), {"canal": CANAL, "charge": _charge_utile(event)}
    )


class RelaisPostgres:
    """Une connexion `LISTEN` par réplique, qui republie dans le bus local. Se reconnecte seule."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
        self._tache: asyncio.Task[None] | None = None
        self.recus = 0

    def demarrer(self) -> None:
        self._tache = asyncio.create_task(self._boucle())

    async def arreter(self) -> None:
        if self._tache is not None:
            self._tache.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._tache

    def _recevoir(self, _conn: Any, _pid: int, _canal: str, charge: str) -> None:
        try:
            event = ChoregosEvent.model_validate_json(charge)
        except Exception as exc:
            logger.warning("notification illisible", error=str(exc)[:120])
            return
        self.recus += 1
        _bus.publish(event)

    async def _boucle(self) -> None:
        import asyncpg

        attente = 1.0
        while True:
            try:
                conn = await asyncpg.connect(self.dsn)
                try:
                    await conn.add_listener(CANAL, self._recevoir)
                    logger.info("relais d'événements PostgreSQL branché", canal=CANAL)
                    attente = 1.0
                    while True:
                        await asyncio.sleep(30)
                        await conn.execute(
                            "SELECT 1"
                        )  # une connexion morte se voit ici, pas au prochain événement
                finally:
                    with contextlib.suppress(Exception):
                        await conn.close()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "relais d'événements PostgreSQL perdu, reconnexion", error=str(exc)[:120], dans_s=attente
                )
                await asyncio.sleep(attente)
                attente = min(attente * 2, 30.0)


def sse_format(event: ChoregosEvent) -> dict[str, str]:
    """Rend un événement au format attendu par `sse_starlette.EventSourceResponse`."""
    return {
        "id": event.id,
        "event": str(event.type),
        "data": event.model_dump_json(),
    }
