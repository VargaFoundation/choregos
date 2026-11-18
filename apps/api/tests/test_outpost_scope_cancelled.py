"""A look at an outpost's call that a cancellation interrupts stays a cancellation (S65-29).

`scope_to_outpost` reads an outpost under the all-organisations scope, then narrows the session to the
outpost's organisation. On PostgreSQL a task cancelled in the middle of that read (a worker stopping
while it waits for a write, `rendezvous.wait`) leaves a connection SQLAlchemy has invalidated: any
further statement raises `PendingRollbackError`. The narrowing used to run in a `finally`, so its error
replaced the cancellation, and a worker stopped at shutdown reported a database error instead. Seen on
CI's PostgreSQL job as `test_a_worker_that_stops_while_its_write_is_claimed_does_not_put_it_in_doubt`
raising `PendingRollbackError`, depending on the instant the cancellation landed. Here the instant is
fixed: the read itself is cancelled, by a session that behaves as an invalidated one afterwards.

The same race on a real PostgreSQL (S65-34, #1710): the read is held on a lock another connection
takes, so the cancellation lands while the statement is in flight in the driver, where the CI's
worker met it. The stub shows what the code does with an invalidated session; this shows that a real
cancellation in the middle of the read is what invalidates it, and that nothing is left behind.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import PendingRollbackError
from sqlalchemy.sql.elements import TextClause

from .conftest import APP_ROLE, PG_URL, login, without_postgres
from .kits import outpost_daemon as daemon_kit
from .kits import outposts as kit


class _InterruptedSession:
    """Statements of text (the two settings) pass; the read is cancelled; after it, as after a
    cancellation that reached the driver, every statement raises `PendingRollbackError`."""

    def __init__(self) -> None:
        self.interrupted = False
        self.after: list[str] = []

    async def execute(self, statement: Any, *_args: Any, **_kwargs: Any) -> Any:
        if self.interrupted:
            self.after.append(str(statement))
            raise PendingRollbackError("Can't reconnect until invalid transaction is rolled back.")
        if isinstance(statement, TextClause):
            return SimpleNamespace(first=lambda: None)
        self.interrupted = True
        raise asyncio.CancelledError


async def test_a_read_cancelled_in_the_middle_raises_the_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tranibor_api.db import session as db_session
    from tranibor_api.services import outposts

    # The settings are PostgreSQL's: under SQLite the two settings are not statements at all.
    monkeypatch.setattr(db_session, "get_settings", lambda: SimpleNamespace(is_sqlite=False))
    session = _InterruptedSession()
    with pytest.raises(asyncio.CancelledError):
        await outposts.scope_to_outpost(session, "outpost-1")  # type: ignore[arg-type]
    assert session.after == [], "nothing runs on a transaction the cancellation made unusable"


async def test_a_read_that_finds_the_outpost_narrows_the_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    """The way out is unchanged: the outpost found, the session is narrowed to its organisation."""
    from tranibor_api.db import session as db_session
    from tranibor_api.services import outposts

    monkeypatch.setattr(db_session, "get_settings", lambda: SimpleNamespace(is_sqlite=False))
    said: list[dict[str, Any]] = []
    outpost = SimpleNamespace(id="outpost-1")

    class _Session:
        async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
            if isinstance(statement, TextClause):
                said.append(dict(params or {}))
                return SimpleNamespace(first=lambda: None)
            return SimpleNamespace(first=lambda: (outpost, "acme"))

    found = await outposts.scope_to_outpost(_Session(), "outpost-1")  # type: ignore[arg-type]
    assert found == (outpost, "acme")
    assert said[-1] == {"orgs": "acme"}, said


# ───────────────────────── PostgreSQL: the cancellation lands in the driver ─────────────────────────


@pytest.fixture
async def pg_client(pg_app: Any, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    from tranibor_api.config import reset_settings_cache
    from tranibor_api.db.models import Organization
    from tranibor_api.db.session import session_scope

    daemon_kit.key_wrapper(monkeypatch)
    kit.switch_on(monkeypatch)
    monkeypatch.setenv("CHOREGOS_OIDC_DEFAULT_ORG", kit.ORG)
    monkeypatch.setenv("CHOREGOS_DEV_ADMIN_EMAILS", "admin@northwind.dev")
    reset_settings_cache()
    async with session_scope(orgs="*") as session:
        session.add(Organization(slug=kit.ORG, name="Northwind"))
    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as http:
        await login(http, "admin@northwind.dev")
        yield http


def _dsn() -> str:
    return PG_URL.replace("postgresql+asyncpg://", "postgresql://")


async def _the_apps(connection: Any, condition: str) -> list[str]:
    """The application role's backends that match `condition`, as the server sees them."""
    rows = await connection.fetch(
        "SELECT left(query, 120) FROM pg_stat_activity "
        f"WHERE usename = $1 AND datname = current_database() AND {condition}",
        APP_ROLE,
    )
    return [str(row[0]) for row in rows]


async def _cancelled_on_a_lock(watcher: Any, table: str, work: Any) -> None:
    """Run `work` while another connection holds `table`, cancel it once one of its statements waits
    for that table on the server, and check that it raises its cancellation and no other error."""
    import asyncpg

    holder = await asyncpg.connect(_dsn())
    try:
        locked = holder.transaction()
        await locked.start()
        await holder.execute(f"LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE")
        task = asyncio.ensure_future(work)
        deadline = time.monotonic() + 20
        while not await _the_apps(watcher, f"wait_event_type = 'Lock' AND query ILIKE '%{table}%'"):
            assert not task.done(), task
            assert time.monotonic() < deadline, f"no statement of the work ever waited for {table}"
            await asyncio.sleep(0.05)
        # The worker stops while the statement is in flight.
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await locked.rollback()
    finally:
        await holder.close()
    # Nothing the cancelled work began is left open on the server: no transaction held, no lock
    # waited for.
    deadline = time.monotonic() + 20
    while left := await _the_apps(watcher, "state <> 'idle'"):
        assert time.monotonic() < deadline, f"left behind by the cancelled work: {left}"
        await asyncio.sleep(0.05)


@without_postgres
async def test_a_look_cancelled_while_postgresql_runs_its_read_raises_the_cancellation(
    pg_client: AsyncClient,
) -> None:
    """A worker that waits for a write through an outpost (`rendezvous.wait`) is stopped while the
    look's read of the outpost waits on the server. It sees its cancellation, not a database error;
    the connection the cancellation spoiled is thrown away, never handed to the next look; and the
    next look works. Red before S65-29: `PendingRollbackError`, as on the CI's runner (#1710)."""
    import asyncpg
    from tranibor_api.db.session import get_engine
    from tranibor_api.outposts import rendezvous

    await kit.create(pg_client, "factory-floor")
    outpost_id = str((await kit.outpost_row("factory-floor")).id)
    watcher = await asyncpg.connect(_dsn())
    try:
        await _cancelled_on_a_lock(watcher, "outposts", rendezvous.wait(outpost_id, rendezvous.new_call_id()))
    finally:
        await watcher.close()
    assert get_engine().pool.checkedout() == 0  # type: ignore[attr-defined]
    # The next look takes a connection from the same pool and reads the outpost: there is no such
    # call, which is an answer, not an error.
    async with rendezvous.in_scope(outpost_id) as scope:
        assert scope is not None and scope.outpost.id == outpost_id
    assert await asyncio.wait_for(rendezvous.wait(outpost_id, rendezvous.new_call_id()), timeout=10) is None


@without_postgres
async def test_a_placement_cancelled_while_postgresql_runs_it_raises_the_cancellation(
    pg_client: AsyncClient,
) -> None:
    """The other end of the write path: a worker stopped while it places its write. The placement
    names the outpost for a block (`in_outpost_scope`) and names again what was named before when the
    block ends; after a cancellation in the driver that second statement met a transaction nobody may
    use, and its `PendingRollbackError` replaced the cancellation. Nothing was placed, and the same
    call placed again is placed once."""
    import asyncpg
    from tranibor_api.db.models import OutpostCall
    from tranibor_api.db.session import get_engine

    await kit.create(pg_client, "factory-floor")
    call_id = "placed-by-a-worker-that-stops"
    watcher = await asyncpg.connect(_dsn())
    try:
        await _cancelled_on_a_lock(watcher, "outpost_calls", daemon_kit.place(call_id=call_id))
    finally:
        await watcher.close()
    assert get_engine().pool.checkedout() == 0  # type: ignore[attr-defined]
    assert await kit.rows_of("factory-floor", OutpostCall) == []
    placed = await daemon_kit.place(call_id=call_id)
    assert placed.created and placed.id == call_id
    assert [row.id for row in await kit.rows_of("factory-floor", OutpostCall)] == [call_id]
