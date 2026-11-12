"""Agent session responses held at their model do not exhaust a replica's pool (#1376), on a real
PostgreSQL.

A prompt session's response used to hold one pooled connection `idle in transaction` for its whole
loop: each model call may take 300 s, and a handful of people talking to agents at once filled a
replica's pool, every other request of the replica waiting `db_pool_timeout_s`, then failing. The
first response of a session also held its budget's advisory lock that long, so a second session of
the same project could not even open. Here the pool is two connections with no overflow and a
two-second wait; three responses of three sessions are held at the model. Without the fix the third
never reaches the model and `/readyz` waits out the pool; with it, the three wait at the model
holding nothing, `pg_stat_activity` shows no session of the application role idle in transaction,
and each response is booked once the model answers, under RLS.

Runs only against a real PostgreSQL (`CHOREGOS_TEST_DATABASE_URL`), as the application role (not a
superuser, so RLS applies); the CI starts one, locally `docs/development.md` says how.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from .conftest import APP_ROLE, PG_URL, login, without_postgres

pytestmark = [without_postgres, pytest.mark.asyncio]

POOL_SIZE = 2
POOL_TIMEOUT_S = 2
HELD = 3
ORG = "/api/v1/orgs/a"


class HeldModel:
    """The model behind the gateway, answering only once `release` is set."""

    def __init__(self) -> None:
        self.arrived = 0
        self.release = asyncio.Event()

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.arrived += 1
        await self.release.wait()
        completion = {
            "choices": [{"message": {"role": "assistant", "content": "Order 42 shipped."}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3},
        }
        return httpx.Response(200, json=completion, request=request)


@pytest.fixture
def held() -> Iterator[HeldModel]:
    from choregos_adapters.fakes import FakeGateway
    from choregos_api.services.agent_sessions import set_model_transport, set_session_gateway

    model = HeldModel()
    set_session_gateway(FakeGateway())
    set_model_transport(httpx.MockTransport(model.handle))
    yield model
    set_session_gateway(None)
    set_model_transport(None)


async def _small_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """The engine built again with a pool of `POOL_SIZE`, no overflow, a short wait."""
    from choregos_api.config import reset_settings_cache
    from choregos_api.db import session as db_session

    monkeypatch.setenv("CHOREGOS_DB_POOL_SIZE", str(POOL_SIZE))
    monkeypatch.setenv("CHOREGOS_DB_MAX_OVERFLOW", "0")
    monkeypatch.setenv("CHOREGOS_DB_POOL_TIMEOUT_S", str(POOL_TIMEOUT_S))
    reset_settings_cache()
    await db_session.dispose_engine()


async def _an_agent_to_talk_to(client: AsyncClient) -> None:
    """The organisation `a`, its administrator signed in, a project and a prompt agent."""
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        session.add(Organization(slug="a", name="A"))
    await login(client, "admin@a.test")
    project = {"slug": "billing-api", "name": "Billing", "config": {"slug": "billing-api", "org": "a"}}
    created = await client.post(f"{ORG}/projects", json=project)
    assert created.status_code == 201, created.text
    spec = {"instructions": "You answer questions about orders.", "runtime": {"kind": "prompt"}}
    created = await client.post(f"{ORG}/agents", json={"slug": "helper", "display_name": "H", "spec": spec})
    assert created.status_code == 201, created.text


async def _idle_in_transaction() -> int:
    """The application role's sessions idle in an open transaction, seen as a superuser."""
    import asyncpg

    connection = await asyncpg.connect(PG_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        return int(
            await connection.fetchval(
                "SELECT count(*) FROM pg_stat_activity WHERE usename = $1 AND state = 'idle in transaction'"
                " AND datname = current_database()",
                APP_ROLE,
            )
        )
    finally:
        await connection.close()


async def test_responses_held_at_their_model_leave_the_pool_to_the_other_requests(
    pg_app: Any, held: HeldModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.db.models import AgentSessionEvent, AgentSessionRecord
    from choregos_api.db.session import session_scope

    await _small_pool(monkeypatch)
    loop = asyncio.get_running_loop()

    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
        await _an_agent_to_talk_to(client)
        responses = [
            asyncio.create_task(
                client.post(
                    f"{ORG}/agents/helper/responses",
                    json={"project": "billing-api", "input": f"Where is order {index}?"},
                )
            )
            for index in range(HELD)
        ]
        try:
            # Every response has reached the model, or given up on the pool and answered.
            deadline = loop.time() + 30
            while held.arrived + sum(response.done() for response in responses) < HELD:
                assert loop.time() < deadline, "responses neither reached the model nor ended"
                await asyncio.sleep(0.01)
            ended = [response.result().text for response in responses if response.done()]
            assert held.arrived == HELD, f"{held.arrived} responses reached the model: {ended}"

            # A fourth request, a read that needs a connection, is served at once.
            started = loop.time()
            ready = await asyncio.wait_for(client.get("/readyz"), timeout=POOL_TIMEOUT_S * 5)
            waited = loop.time() - started
            assert ready.status_code == 200, ready.text
            assert waited < POOL_TIMEOUT_S, f"/readyz waited {waited:.2f} s for a connection"
            assert await _idle_in_transaction() == 0, "a response holds a connection idle in transaction"
        finally:
            held.release.set()
            answers = await asyncio.gather(*responses, return_exceptions=True)

    for answer in answers:
        assert isinstance(answer, httpx.Response), answer
        assert answer.status_code == 200 and answer.json()["status"] == "completed", answer.text
    # Booked after the model answered, in a transaction that set the caller's scope again: under RLS,
    # a journal line or a count written without it would have been refused.
    async with session_scope(orgs="*") as session:
        records = (await session.execute(select(AgentSessionRecord))).scalars().all()
        journal = select(AgentSessionEvent.session_id, AgentSessionEvent.type).order_by(AgentSessionEvent.seq)
        lines = (await session.execute(journal)).all()
    assert sorted(record.id for record in records) == sorted(a.json()["session_id"] for a in answers)
    assert {record.responses for record in records} == {1}
    for record in records:
        assert [m["role"] for m in record.messages] == ["system", "user", "assistant"]
        assert [kind for session_id, kind in lines if session_id == record.id] == [
            "session.opened",
            "input.text",
            "context.breakdown",  # the only call, kept at once (ADR 0141 §5)
            "prompt.turn",
            "response.completed",
        ]
