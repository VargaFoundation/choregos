"""`connector_operations.result_max_bytes` (S40-17, migration `b7d1f3a5c9e2`), up and down (S40-17b).

S40-17 proved its migration on SQLite only. The same round trip runs here on SQLite and, when a
PostgreSQL is there, as the application role under forced RLS (S39-27): an operation declared before
the migration keeps the default, takes a cap after it, loses only the column on the way down, and
comes back up with the default again.
"""

from __future__ import annotations

import asyncio
import pathlib
from collections.abc import Callable
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from choregos_api.config import get_settings
from sqlalchemy import create_engine, inspect, text

from .conftest import PG_URL, _preparer_postgres, _url_app, sans_postgres

API = pathlib.Path(__file__).resolve().parents[1]
BEFORE = "e4a6c8d0b2f1"
CAP = "b7d1f3a5c9e2"


def _config(url: str) -> Config:
    config = Config(str(API / "alembic.ini"))
    config.set_main_option("script_location", str(API / "src" / "choregos_api" / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _row(connection: Any, table: str, **columns: object) -> None:
    names = [*columns, "created_at", "updated_at"]
    values = [f":{c}" for c in columns] + ["CURRENT_TIMESTAMP"] * 2
    connection.execute(
        text(f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join(values)})"), columns
    )


def _before(connection: Any) -> None:
    """One read operation, declared before the cap existed."""
    _row(connection, "organizations", id="o1", slug="varga", name="V", settings="{}")
    _row(
        connection,
        "org_connectors",
        id="c1",
        org_id="o1",
        name="logs",
        kind="mcp",
        type="mcp",
        config="{}",
        status="ok",
        availability="all_projects",
    )
    _row(
        connection,
        "connector_operations",
        id="op1",
        org_id="o1",
        connector_id="c1",
        name="read_logs",
        access="read",
        policy="allowed",
        groups="[]",
    )


def _cap(connection: Any) -> Any:
    return connection.execute(
        text("SELECT result_max_bytes FROM connector_operations WHERE id = 'op1'")
    ).scalar_one()


def _set_cap(connection: Any) -> Any:
    connection.execute(text("UPDATE connector_operations SET result_max_bytes = 4096 WHERE id = 'op1'"))
    return _cap(connection)


def _state(connection: Any) -> tuple[bool, list[str]]:
    columns = {c["name"] for c in inspect(connection).get_columns("connector_operations")}
    names = [str(n) for (n,) in connection.execute(text("SELECT name FROM connector_operations"))]
    return "result_max_bytes" in columns, names


def _round_trip(url: str, work: Callable[[Callable[[Any], Any]], Any]) -> list[Any]:
    """Upgrade, write, downgrade, upgrade again; what each step saw."""
    seen: list[Any] = []
    command.upgrade(_config(url), BEFORE)
    work(_before)
    command.upgrade(_config(url), CAP)
    seen.append(work(_cap))
    seen.append(work(_set_cap))
    command.downgrade(_config(url), BEFORE)
    seen.append(work(_state))
    command.upgrade(_config(url), CAP)
    seen.append(work(_cap))
    return seen


#: An existing operation keeps the default; a cap is stored; down drops the column and keeps the
#: operation; up again reads the default, not a leftover.
EXPECTED = [None, 4096, (False, ["read_logs"]), None]


def test_the_cap_migration_goes_up_and_down_on_sqlite(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "cap.db"
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{database}")
    get_settings.cache_clear()
    url = f"sqlite:///{database}"
    engine = create_engine(url)

    def work(step: Callable[[Any], Any]) -> Any:
        with engine.begin() as connection:
            return step(connection)

    try:
        seen = _round_trip(url, work)
    finally:
        engine.dispose()
        get_settings.cache_clear()
    assert seen == EXPECTED


@sans_postgres
def test_the_cap_migration_goes_up_and_down_on_postgresql_as_the_application_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    asyncio.run(_preparer_postgres())
    url = _url_app(PG_URL)
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", url)
    get_settings.cache_clear()

    async def as_the_application(step: Callable[[Any], Any]) -> Any:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                # The test sees every organisation; what is under test is the migration.
                await connection.execute(text("SELECT set_config('app.current_orgs', '*', true)"))
                return await connection.run_sync(step)
        finally:
            await engine.dispose()

    try:
        seen = _round_trip(url, lambda step: asyncio.run(as_the_application(step)))
    finally:
        get_settings.cache_clear()
    assert seen == EXPECTED
