"""An event born in a transaction reaches the SSE subscribers of ANOTHER replica.

The bus was in the memory of one process (state of play of 2026-09-24): the orchestrator's events
never reached the API, and two replicas did not see each other. This test only runs on
PostgreSQL: it is `NOTIFY` that carries the event, at commit.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select

from .conftest import PG_URL, without_postgres

pytestmark = without_postgres


async def test_a_persisted_event_crosses_the_replicas(pg_app: Any) -> None:
    from choregos_api.db.models import Organization, Project
    from choregos_api.db.session import session_scope
    from choregos_api.events import PostgresRelay, get_bus
    from choregos_api.services import persist_event
    from choregos_contracts import EventType

    async with session_scope(orgs="*") as session:
        org = Organization(slug="a", name="A")
        session.add(org)
        await session.flush()
        project = Project(
            org_id=org.id, slug="p", name="P", status="active", config={"slug": "p", "org": "a"}
        )
        session.add(project)
        await session.flush()
        project_id = project.id

    # "the other replica": one more relay, attached to the same database
    relay = PostgresRelay(PG_URL)
    relay.start()
    try:
        await asyncio.sleep(0.5)  # time for LISTEN to be set
        subscription = get_bus().subscribe("project:p")
        received: list[Any] = []

        async def listen() -> None:
            async for event in subscription:
                received.append(event)
                break

        listening = asyncio.create_task(listen())
        await asyncio.sleep(0.1)
        async with session_scope(orgs="*") as session:
            await persist_event(
                session,
                EventType.WORKITEM_CREATED,
                project_id=project_id,
                project_slug="p",
                subject="p-1",
                key="p-1",
            )
            # not committed yet: nothing must have arrived
            await asyncio.sleep(0.3)
            assert received == [], "an event does not leave before the commit"
        await asyncio.wait_for(listening, timeout=5.0)
    finally:
        await relay.stop()
    assert len(received) == 1
    assert str(received[0].type) == str(EventType.WORKITEM_CREATED)
    assert received[0].subject == "p-1" and received[0].data["key"] == "p-1"
    assert relay.received >= 1

    async with session_scope(orgs="*") as session:
        from choregos_api.db.models import Event

        assert (await session.execute(select(Event).where(Event.subject == "p-1"))).scalar_one()
