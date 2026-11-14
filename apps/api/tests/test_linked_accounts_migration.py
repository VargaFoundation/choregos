"""`tracker_identities` becomes `linked_accounts` (S66-41, ADR 0161): every link is copied, unverified.

The GitHub links an administrator declared (ADR 0102) become `github` links at `https://github.com`, NOT
verified, with their id, author and timestamps; the downgrade brings them back. Checked on SQLite and, when
a PostgreSQL is there, as the application role under forced RLS, where a copy without the every-organisation
scope would copy nothing and still end green.
"""

from __future__ import annotations

import asyncio
import pathlib
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from choregos_api.config import get_settings
from sqlalchemy import create_engine, text

from .conftest import PG_URL, _app_url, _prepare_postgres, without_postgres

API = pathlib.Path(__file__).resolve().parents[1]
BEFORE = "f9b1d3e5a7c0"
LINKED = "c7e2a9d4f1b6"


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
    for org in ("o1", "o2"):
        _row(connection, "organizations", id=org, slug=f"org-{org}", name=org, settings="{}")
    for user in ("u1", "u2"):
        _row(
            connection,
            "users",
            id=user,
            email=f"{user}@varga.dev",
            display_name=user,
            is_service=False,
            groups="[]",
        )
    _row(connection, "tracker_identities", id="t1", org_id="o1", user_id="u1", tracker="github",
         external_id="1001", login="devon", created_by="admin@varga.dev")  # fmt: skip
    _row(connection, "tracker_identities", id="t2", org_id="o2", user_id="u2", tracker="github",
         external_id="1001", login="other", created_by=None)  # fmt: skip


def _linked(connection: Any) -> list[tuple[Any, ...]]:
    rows = connection.execute(
        text(
            "SELECT id, org_id, user_id, provider, base_url, external_id, login, verified, verified_at, "
            "connector, created_by FROM linked_accounts ORDER BY id"
        )
    )
    return [(*row[:7], bool(row[7]), *row[8:]) for row in rows]


EXPECTED = [
    ("t1", "o1", "u1", "github", "https://github.com", "1001", "devon", False, None, None, "admin@varga.dev"),
    ("t2", "o2", "u2", "github", "https://github.com", "1001", "other", False, None, None, None),
]


def test_the_migration_copies_every_declared_link_unverified_and_back(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "linked.db"
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{database}")
    get_settings.cache_clear()
    url = f"sqlite:///{database}"
    try:
        command.upgrade(_config(url), BEFORE)
        engine = create_engine(url)
        with engine.begin() as connection:
            _before(connection)
        command.upgrade(_config(url), LINKED)
        with engine.connect() as connection:
            linked = _linked(connection)
            tables = {
                r[0] for r in connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            }
        command.downgrade(_config(url), BEFORE)
        with engine.connect() as connection:
            back = connection.execute(
                text(
                    "SELECT id, org_id, user_id, tracker, external_id, login "
                    "FROM tracker_identities ORDER BY id"
                )
            ).all()
            tables_after = {
                r[0] for r in connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            }
        command.upgrade(_config(url), "head")
    finally:
        get_settings.cache_clear()
    assert linked == EXPECTED
    assert "tracker_identities" not in tables
    assert [tuple(r) for r in back] == [
        ("t1", "o1", "u1", "github", "1001", "devon"),
        ("t2", "o2", "u2", "github", "1001", "other"),
    ]
    assert "linked_accounts" not in tables_after


@without_postgres
def test_the_copy_sees_every_organisation_as_the_application_role_under_forced_rls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    asyncio.run(_prepare_postgres())
    url = _app_url(PG_URL)
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", url)
    get_settings.cache_clear()

    async def as_the_application(work: Any) -> Any:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                await connection.execute(text("SELECT set_config('app.current_orgs', '*', true)"))
                return await connection.run_sync(work)
        finally:
            await engine.dispose()

    async def forced(table: str) -> tuple[bool, bool]:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                row = (
                    await connection.execute(
                        text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :t"),
                        {"t": table},
                    )
                ).one()
                return bool(row[0]), bool(row[1])
        finally:
            await engine.dispose()

    try:
        command.upgrade(_config(url), BEFORE)
        asyncio.run(as_the_application(_before))
        command.upgrade(_config(url), LINKED)
        linked = asyncio.run(as_the_application(_linked))
        protected = asyncio.run(forced("linked_accounts"))
    finally:
        get_settings.cache_clear()
    assert linked == EXPECTED
    assert protected == (True, True)
