"""The long poll holds no connection while it waits, and is woken by the commit of a call
(S65-12, ADR 0148 §7).

An outpost keeps one poll open per instance, for 25 seconds at a time. A poll that held a transaction
while it waited would hold a pooled connection too: a few dozen instances would take the whole pool
from the console and the runs. So a poll commits what it wrote, waits with nothing in hand, and looks
in a short transaction of its own, on the model of the hosted sessions' `turns/next`. It is woken
through the `LISTEN` connection every replica already holds, on a channel of its own, and looks again
periodically whatever it heard: a notification can be lost across a reconnection.

Only PostgreSQL has a pool and a `NOTIFY`: these tests run with `CHOREGOS_TEST_DATABASE_URL`, on the
migrated schema, as the application role.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from .conftest import APP_ROLE, PG_URL, login, without_postgres
from .kits import outpost_daemon as daemon_kit
from .kits import outposts as kit
from .kits.outpost_daemon import SLUG, Daemon

pytestmark = without_postgres


@pytest.fixture(autouse=True)
def door(monkeypatch: pytest.MonkeyPatch) -> kit.Door:
    daemon_kit.key_wrapper(monkeypatch)
    return kit.switch_on(monkeypatch)


@pytest.fixture
async def client(pg_app: Any, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    from choregos_api.config import reset_settings_cache
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope

    monkeypatch.setenv("CHOREGOS_OIDC_DEFAULT_ORG", kit.ORG)
    monkeypatch.setenv("CHOREGOS_DEV_ADMIN_EMAILS", "admin@varga.dev")
    reset_settings_cache()
    async with session_scope(orgs="*") as session:
        session.add(Organization(slug=kit.ORG, name="Varga Foundation"))
    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as http:
        await login(http, "admin@varga.dev")
        yield http


@pytest.fixture
async def daemon(client: AsyncClient) -> Daemon:
    return await daemon_kit.approved(client)


@contextlib.asynccontextmanager
async def _relay() -> AsyncIterator[Any]:
    """This replica's `LISTEN` connection, as the lifespan starts it when outposts are on."""
    from choregos_api.config import get_settings
    from choregos_api.events import PostgresRelay
    from choregos_api.outposts import rendezvous

    relay = PostgresRelay(get_settings().database_url)
    relay.listen_also(rendezvous.CHANNEL, rendezvous.woken)
    relay.start()
    try:
        await asyncio.wait_for(relay.attached.wait(), timeout=10)
        yield relay
    finally:
        await relay.stop()


async def _until(condition: Any, timeout_s: float = 20.0) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        found = condition()
        if asyncio.iscoroutine(found):
            found = await found
        if found:
            return
        assert time.monotonic() < deadline, "it never happened"
        await asyncio.sleep(0.05)


async def _waits(daemon: Daemon) -> bool:
    return (await daemon_kit.instance_row(str(daemon.instance_id))).poll_holder is not None


async def _busy_connections() -> list[tuple[str, str]]:
    """The application role's connections that are doing or holding something, as the server sees them."""
    import asyncpg

    connection = await asyncpg.connect(PG_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        rows = await connection.fetch(
            "SELECT state, left(query, 80) FROM pg_stat_activity "
            "WHERE usename = $1 AND datname = current_database() AND state <> 'idle'",
            APP_ROLE,
        )
        return [(str(row[0]), str(row[1])) for row in rows]
    finally:
        await connection.close()


async def _connections() -> int:
    import asyncpg

    connection = await asyncpg.connect(PG_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        return int(
            await connection.fetchval(
                "SELECT count(*) FROM pg_stat_activity WHERE usename = $1 AND datname = current_database()",
                APP_ROLE,
            )
        )
    finally:
        await connection.close()


async def test_a_hundred_waiting_polls_hold_no_pooled_connection(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import get_settings
    from choregos_api.db.models import OutpostInstance
    from choregos_api.db.session import get_engine
    from choregos_api.outposts import rendezvous

    await kit.create(client, SLUG)
    daemons = await daemon_kit.instances(100)
    key = rendezvous.calls_key((await kit.outpost_row(SLUG)).id)
    monkeypatch.setattr(rendezvous, "LOOK_S", (60.0, 60.0))  # they look once, then only wait
    settings = get_settings()
    bound = settings.db_pool_size + settings.db_max_overflow
    polls = [asyncio.create_task(daemon.poll(client, wait_s=25)) for daemon in daemons]
    try:

        async def all_wait() -> bool:
            rows = await kit.rows_of(SLUG, OutpostInstance)
            return sum(1 for row in rows if row.poll_holder is not None) == 100

        await _until(all_wait)
        await _until(lambda: rendezvous.waiters().waiting(key) == 100)
        pool = get_engine().pool
        await _until(lambda: pool.checkedout() == 0)  # type: ignore[attr-defined]
        # A hundred polls wait, and for a while nothing changes: no connection is out of the pool, and
        # the server sees none of the role's connections in a transaction.
        for _ in range(10):
            assert pool.checkedout() == 0  # type: ignore[attr-defined]
            assert await _busy_connections() == []
            await asyncio.sleep(0.1)
        assert not [poll for poll in polls if poll.done()], "they are all still waiting"
        # A hundred polls came through a pool that never grew past its bound.
        assert await _connections() <= bound < 100
    finally:
        for poll in polls:
            poll.cancel()
        await asyncio.gather(*polls, return_exceptions=True)

    async def all_gave_back() -> bool:
        return not [row for row in await kit.rows_of(SLUG, OutpostInstance) if row.poll_holder is not None]

    await _until(all_gave_back)
    assert rendezvous.waiters().waiting(key) == 0


async def test_a_call_written_is_handed_over_within_a_second(
    client: AsyncClient, daemon: Daemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Woken by the notification that left with the call's commit, at whichever replica the poll waits."""
    from choregos_api.outposts import rendezvous

    monkeypatch.setattr(rendezvous, "LOOK_S", (60.0, 60.0))  # only a notification can wake it in time
    async with _relay():
        waiting = asyncio.create_task(daemon.poll(client, wait_s=25))
        await _until(lambda: _waits(daemon))
        await asyncio.sleep(0.5)
        assert not waiting.done()
        heard = rendezvous.waiters().heard
        started = time.monotonic()
        placed = await daemon_kit.place()
        response = await asyncio.wait_for(waiting, timeout=10)
        elapsed = time.monotonic() - started
        assert [call["id"] for call in response.json()["calls"]] == [placed.id]
        assert elapsed < 1.0, f"handed over {elapsed:.2f} s after it was written"
        assert rendezvous.waiters().heard > heard, "it came through NOTIFY"

        # The other way: whoever placed the call is woken when the door records its answer.
        (call,) = [daemon.opened(handed) for handed in response.json()["calls"]]
        outpost_id = (await kit.outpost_row(SLUG)).id
        outcome = asyncio.create_task(rendezvous.wait(outpost_id, placed.id))
        await asyncio.sleep(0.5)
        assert not outcome.done()
        started = time.monotonic()
        assert (await daemon.answer(client, call)).status_code == 200
        read = await asyncio.wait_for(outcome, timeout=10)
        assert read is not None and read.answer is not None
        assert time.monotonic() - started < 1.0


async def test_a_call_is_not_handed_over_before_it_is_committed(
    client: AsyncClient, daemon: Daemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.outposts import rendezvous

    monkeypatch.setattr(rendezvous, "LOOK_S", (0.05, 0.1))  # it looks all the time, and finds nothing
    row = await kit.outpost_row(SLUG)
    async with _relay():
        waiting = asyncio.create_task(daemon.poll(client, wait_s=25))
        await _until(lambda: _waits(daemon))
        async with session_scope(orgs=[kit.ORG]) as session:
            placed = await rendezvous.place(
                session,
                row,
                call_id="not-yet",
                kind=rendezvous.OutpostCallKind.LIST_TOOLS,
                endpoint="tracker",
                timeout_s=30,
            )
            await asyncio.sleep(0.6)
            assert not waiting.done(), "the row does not exist for the door until its transaction commits"
        response = await asyncio.wait_for(waiting, timeout=5)
        assert [call["id"] for call in response.json()["calls"]] == [placed.id]


async def test_without_the_relay_a_call_is_still_handed_over_at_the_periodic_look(
    client: AsyncClient, daemon: Daemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A notification can be lost across a reconnection, and a relay can be down: a poll looks again
    every few seconds whatever it heard, so a call waits for the next look at most."""
    from choregos_api.outposts import rendezvous

    monkeypatch.setattr(rendezvous, "LOOK_S", (1.0, 1.5))
    async with _relay() as relay:
        assert relay.attached.is_set()
    assert not relay.attached.is_set(), "the relay is stopped"
    heard = rendezvous.waiters().heard
    waiting = asyncio.create_task(daemon.poll(client, wait_s=25))
    await _until(lambda: _waits(daemon))
    await asyncio.sleep(0.2)
    started = time.monotonic()
    placed = await daemon_kit.place()
    response = await asyncio.wait_for(waiting, timeout=10)
    elapsed = time.monotonic() - started
    assert [call["id"] for call in response.json()["calls"]] == [placed.id]
    assert elapsed < 1.5 + 1.0, f"within the periodic look: {elapsed:.2f} s"
    assert rendezvous.waiters().heard == heard, "nothing was heard: the look found it"


async def test_the_relay_carries_the_wake_ups_beside_the_events_and_survives_a_bad_listener(
    client: AsyncClient, daemon: Daemon
) -> None:
    """One connection per replica, two channels: a wake-up is not a platform event, and what one
    channel's listener does with a payload never takes the connection down."""
    from choregos_api.db.session import session_scope
    from choregos_api.events import CHANNEL, PostgresRelay
    from choregos_api.outposts import rendezvous
    from sqlalchemy import text

    relay = PostgresRelay(PG_URL)
    heard: list[str] = []

    def listener(payload: str) -> None:
        heard.append(payload)
        if payload == "break":
            raise RuntimeError("a listener that fails")

    relay.listen_also(rendezvous.CHANNEL, listener)
    relay.start()
    try:
        await asyncio.wait_for(relay.attached.wait(), timeout=10)
        async with session_scope(orgs="*") as session:
            for payload in ("break", "an-outpost-id"):
                await session.execute(
                    text("SELECT pg_notify(:channel, :payload)"),
                    {"channel": rendezvous.CHANNEL, "payload": payload},
                )
            # Not an event: the events' channel drops it, and it is not a wake-up either.
            await session.execute(text("SELECT pg_notify(:channel, 'an-outpost-id')"), {"channel": CHANNEL})
        await _until(lambda: heard == ["break", "an-outpost-id"])
        assert relay.received == 0 and relay.attached.is_set()
    finally:
        await relay.stop()


async def test_the_api_listens_for_the_wake_ups_when_outposts_are_on(
    pg_app: Any, client: AsyncClient
) -> None:
    """The lifespan gives the replica's `LISTEN` connection the outposts' channel: no test starts it."""
    from choregos_api.db.session import session_scope
    from choregos_api.outposts import rendezvous
    from sqlalchemy import text

    heard = rendezvous.waiters().heard
    async with pg_app.router.lifespan_context(pg_app):
        for _ in range(100):
            async with session_scope(orgs="*") as session:
                await session.execute(
                    text("SELECT pg_notify(:channel, 'an-outpost-id')"), {"channel": rendezvous.CHANNEL}
                )
            await asyncio.sleep(0.1)
            if rendezvous.waiters().heard > heard:
                break
    assert rendezvous.waiters().heard > heard, "the API started without the outposts' channel on its relay"
