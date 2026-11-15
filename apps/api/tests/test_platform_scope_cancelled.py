"""A block under the whole instance's scope that a cancellation interrupts stays a cancellation (S65-36).

`in_platform_scope` widens a session to every organisation for a block, then restores the scope it
had. It used to restore in a `finally`: on PostgreSQL, a task cancelled while a statement of the block
is in flight in the driver leaves a connection SQLAlchemy has invalidated, the restoring statement
raised `PendingRollbackError`, and that error replaced the cancellation (#1743; the same shape as
`in_outpost_scope`, S65-34). The restore is also a security property: the block's scope is the whole
instance, so a block that fails on a usable transaction must still give the narrower scope back.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import PendingRollbackError

from .conftest import APP_ROLE, PG_URL, without_postgres


class _Session:
    """The two settings pass and are remembered; the block's statement is cancelled, after which, as
    after a cancellation that reached the driver, every statement raises `PendingRollbackError`."""

    def __init__(self, before: str) -> None:
        self.before = before
        self.set: list[str] = []
        self.interrupted = False

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        if self.interrupted:
            raise PendingRollbackError("Can't reconnect until invalid transaction is rolled back.")
        sql = str(statement)
        if "current_setting" in sql:
            return SimpleNamespace(scalar_one=lambda: self.before)
        if "set_config" in sql:
            self.set.append((params or {})["orgs"])
            return None
        self.interrupted = True
        raise asyncio.CancelledError


@pytest.fixture
def on_postgresql(monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api.db import session as db_session

    # Under SQLite the settings are not statements at all.
    monkeypatch.setattr(db_session, "get_settings", lambda: SimpleNamespace(is_sqlite=False))


async def test_a_block_cancelled_in_the_driver_raises_the_cancellation(on_postgresql: None) -> None:
    """Red before S65-36: the restore's `PendingRollbackError` replaced the `CancelledError`."""
    from choregos_api.db.session import in_platform_scope

    session = _Session(before="acme")
    with pytest.raises(asyncio.CancelledError):
        async with in_platform_scope(session):  # type: ignore[arg-type]
            await session.execute(text("SELECT count(*) FROM organizations"))
    assert session.set == ["*"]


async def test_a_block_that_raises_on_a_usable_transaction_gets_the_narrow_scope_back(
    on_postgresql: None,
) -> None:
    """An error that is not the database's leaves the transaction usable: the scope of one
    organisation comes back, never the whole instance's."""
    from choregos_api.db.session import in_platform_scope

    session = _Session(before="acme")
    with pytest.raises(LookupError):
        async with in_platform_scope(session):  # type: ignore[arg-type]
            raise LookupError("no such user")
    assert session.set == ["*", "acme"]


async def test_a_block_that_ends_normally_gets_the_narrow_scope_back(on_postgresql: None) -> None:
    from choregos_api.db.session import in_platform_scope

    session = _Session(before="acme")
    async with in_platform_scope(session):  # type: ignore[arg-type]
        pass
    assert session.set == ["*", "acme"]


# ───────────────────────── PostgreSQL: the cancellation lands in the driver ─────────────────────────


def _dsn() -> str:
    return PG_URL.replace("postgresql+asyncpg://", "postgresql://")


async def _the_apps(connection: Any, condition: str) -> list[str]:
    rows = await connection.fetch(
        "SELECT left(query, 120) FROM pg_stat_activity "
        f"WHERE usename = $1 AND datname = current_database() AND {condition}",
        APP_ROLE,
    )
    return [str(row[0]) for row in rows]


@without_postgres
async def test_a_block_cancelled_while_postgresql_runs_it_raises_the_cancellation(pg_app: Any) -> None:
    """A request that counts the organisations under the whole instance's scope is cancelled while
    its read waits on a lock another connection holds. It sees its cancellation, the spoiled
    connection is thrown away, and the next block on the same pool works."""
    import asyncpg
    from choregos_api.db.session import get_engine, in_platform_scope, session_scope

    async def count() -> int:
        async with session_scope(orgs="a") as session, in_platform_scope(session):
            return int((await session.execute(text("SELECT count(*) FROM organizations"))).scalar_one())

    watcher = await asyncpg.connect(_dsn())
    holder = await asyncpg.connect(_dsn())
    try:
        locked = holder.transaction()
        await locked.start()
        await holder.execute("LOCK TABLE organizations IN ACCESS EXCLUSIVE MODE")
        task = asyncio.ensure_future(count())
        deadline = time.monotonic() + 20
        while not await _the_apps(watcher, "wait_event_type = 'Lock' AND query ILIKE '%organizations%'"):
            assert not task.done(), task
            assert time.monotonic() < deadline, "the block's read never waited for the lock"
            await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await locked.rollback()
        deadline = time.monotonic() + 20
        while left := await _the_apps(watcher, "state <> 'idle'"):
            assert time.monotonic() < deadline, f"left behind by the cancelled block: {left}"
            await asyncio.sleep(0.05)
    finally:
        await holder.close()
        await watcher.close()
    assert get_engine().pool.checkedout() == 0  # type: ignore[attr-defined]
    assert await count() >= 0


@without_postgres
async def test_on_postgresql_a_block_that_raises_restores_the_scope_it_found(pg_app: Any) -> None:
    from choregos_api.db.session import in_platform_scope, session_scope

    async with session_scope(orgs="a") as session:
        with pytest.raises(LookupError):
            async with in_platform_scope(session):
                assert (
                    await session.execute(text("SELECT current_setting('app.current_orgs', true)"))
                ).scalar_one() == "*"
                raise LookupError("no such user")
        scope = (await session.execute(text("SELECT current_setting('app.current_orgs', true)"))).scalar_one()
    assert scope == "a"
