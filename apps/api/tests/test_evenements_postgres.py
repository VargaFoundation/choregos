"""Un événement né dans une transaction arrive aux abonnés SSE d'une AUTRE réplique.

Le bus était en mémoire d'un processus (état des lieux du 2026-09-24) : les événements de
l'orchestrateur n'atteignaient jamais l'API, et deux répliques ne se voyaient pas. Ce test
ne tourne que sur PostgreSQL : c'est `NOTIFY` qui porte l'événement, au commit.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select

from .conftest import PG_URL, sans_postgres

pytestmark = sans_postgres


async def test_un_evenement_persiste_traverse_les_repliques(pg_app: Any) -> None:
    from choregos_api.db.models import Organization, Project
    from choregos_api.db.session import session_scope
    from choregos_api.events import RelaisPostgres, get_bus
    from choregos_api.services import persist_event
    from choregos_contracts import EventType

    async with session_scope(orgs="*") as session:
        org = Organization(slug="a", name="A")
        session.add(org)
        await session.flush()
        projet = Project(org_id=org.id, slug="p", name="P", status="active", config={"slug": "p", "org": "a"})
        session.add(projet)
        await session.flush()
        projet_id = projet.id

    # « l'autre réplique » : un relais de plus, branché sur la même base
    relais = RelaisPostgres(PG_URL)
    relais.demarrer()
    try:
        await asyncio.sleep(0.5)  # le temps que LISTEN soit posé
        abonnement = get_bus().subscribe("project:p")
        recu: list[Any] = []

        async def ecouter() -> None:
            async for event in abonnement:
                recu.append(event)
                break

        ecoute = asyncio.create_task(ecouter())
        await asyncio.sleep(0.1)
        async with session_scope(orgs="*") as session:
            await persist_event(
                session,
                EventType.WORKITEM_CREATED,
                project_id=projet_id,
                project_slug="p",
                subject="p-1",
                key="p-1",
            )
            # pas encore commité : rien ne doit être arrivé
            await asyncio.sleep(0.3)
            assert recu == [], "un événement ne part pas avant le commit"
        await asyncio.wait_for(ecoute, timeout=5.0)
    finally:
        await relais.arreter()
    assert len(recu) == 1
    assert str(recu[0].type) == str(EventType.WORKITEM_CREATED)
    assert recu[0].subject == "p-1" and recu[0].data["key"] == "p-1"
    assert relais.recus >= 1

    async with session_scope(orgs="*") as session:
        from choregos_api.db.models import Event

        assert (await session.execute(select(Event).where(Event.subject == "p-1"))).scalar_one()
