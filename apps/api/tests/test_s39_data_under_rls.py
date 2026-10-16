"""Data migrations see the rows under forced RLS (S39-27).

On the dev tenant three S39 data migrations ran green and changed nothing: the migration job runs as
the application role, the tables are under forced RLS, and none of them set
`app.current_orgs = '*'`. These tests put a database in the state they left (catalogue agent still
`org`, provenance still `template:*`, no egress row) and check that the replay migration
`a7c9e1b3d5f8` repairs it — on SQLite, and, when a PostgreSQL is there, as the NON-superuser
application role, where a missing bypass turns into zero rows. A static guard keeps every later data
migration from forgetting it again.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import re
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from choregos_api.config import get_settings
from choregos_core.catalogue_d_agents import entrees
from sqlalchemy import create_engine, text

from .conftest import PG_URL, _preparer_postgres, _url_app, sans_postgres

API = pathlib.Path(__file__).resolve().parents[1]
VERSIONS = API / "src/choregos_api/migrations/versions"
LEFT = "f3a5c7e9b1d2"
REPLAY = "a7c9e1b3d5f8"
#: Data migrations written before S39-27 that touch no table under RLS, or set the bypass already.
FIRST_GUARDED = "20261015"


def _config(url: str) -> Config:
    config = Config(str(API / "alembic.ini"))
    config.set_main_option("script_location", str(API / "src" / "choregos_api" / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _row(connection: object, table: str, **columns: object) -> None:
    names = [*columns, "created_at", "updated_at"]
    values = [f":{c}" for c in columns] + ["CURRENT_TIMESTAMP"] * 2
    sql = f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join(values)})"
    connection.execute(text(sql), columns)  # type: ignore[attr-defined]


def _left_behind(connection: object) -> str:
    """What the three migrations left on the dev tenant; returns the catalogue slug used."""
    entry = next(iter(entrees()))
    _row(connection, "organizations", id="o1", slug="varga", name="V", settings="{}")
    _row(
        connection,
        "org_connectors",
        id="c1",
        org_id="o1",
        name="supplier",
        kind="mcp",
        type="mcp",
        status="unknown",
        config=json.dumps({"url": "https://mcp.vendor.test/mcp"}),
    )
    # A catalogue agent installed by a template: version 1 authored `template:*`, scope `org`.
    _row(
        connection,
        "agents",
        id="a1",
        org_id="o1",
        slug=entry.slug,
        display_name="X",
        kind="internal",
        status="active",
        lifecycle="active",
        scope="org",
    )
    _row(
        connection,
        "agent_versions",
        id="v1",
        agent_id="a1",
        org_id="o1",
        version=1,
        spec="{}",
        checksum="sha256:something-else",
        created_by="template:github-software-delivery@1.0.0",
    )
    # A catalogue agent installed directly, whose scope the S39-09 backfill never reached.
    other = next(e for e in entrees() if e.slug != entry.slug)
    _row(
        connection,
        "agents",
        id="a2",
        org_id="o1",
        slug=other.slug,
        display_name="Y",
        kind="internal",
        status="active",
        lifecycle="active",
        scope="org",
    )
    _row(
        connection,
        "agent_versions",
        id="v2",
        agent_id="a2",
        org_id="o1",
        version=1,
        spec="{}",
        checksum="sha256:x",
        created_by=f"catalogue:{other.slug}@{other.version}",
    )
    return entry.slug


def _state(connection: object, slug: str) -> tuple[dict[str, str], str, set[str]]:
    scopes = dict(connection.execute(text("SELECT id, scope FROM agents")).all())  # type: ignore[attr-defined]
    author = connection.execute(  # type: ignore[attr-defined]
        text("SELECT created_by FROM agent_versions WHERE id = 'v1'")
    ).scalar_one()
    hosts = {r[0] for r in connection.execute(text("SELECT host FROM org_egress_rules"))}  # type: ignore[attr-defined]
    return scopes, author, hosts


def _check(scopes: dict[str, str], author: str, hosts: set[str], slug: str) -> None:
    assert scopes == {"a1": "platform", "a2": "platform"}
    assert author.startswith(f"catalogue:{slug}@") and author.endswith("!diverged")
    assert "mcp.vendor.test" in hosts


def test_the_replay_repairs_what_the_three_migrations_left_on_sqlite(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "left.db"
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{database}")
    get_settings.cache_clear()
    url = f"sqlite:///{database}"
    try:
        command.upgrade(_config(url), LEFT)
        engine = create_engine(url)
        with engine.begin() as connection:
            slug = _left_behind(connection)
            # A host already allowed by hand stays as it is: the seed skips it.
            _row(
                connection,
                "org_egress_rules",
                id="r1",
                org_id="o1",
                host="mcp.vendor.test",
                ports="[443]",
                used_by='["api"]',
                status="allowed",
                reason="by hand",
                origin="manual",
            )
        command.upgrade(_config(url), REPLAY)
        with engine.connect() as connection:
            scopes, author, hosts = _state(connection, slug)
            manual = connection.execute(
                text("SELECT COUNT(*) FROM org_egress_rules WHERE host = 'mcp.vendor.test'")
            )
            count = manual.scalar_one()
        # Idempotent: a second pass changes nothing.
        command.downgrade(_config(url), LEFT)
        command.upgrade(_config(url), REPLAY)
        with engine.connect() as connection:
            again = _state(connection, slug)
    finally:
        get_settings.cache_clear()
    _check(scopes, author, hosts, slug)
    assert count == 1, "the seed must not duplicate a host that already has a live rule"
    assert again == (scopes, author, hosts)


@sans_postgres
def test_the_replay_sees_the_rows_as_the_application_role_under_forced_rls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The case of the dev tenant: the job runs as the application role, not a superuser."""
    from sqlalchemy.ext.asyncio import create_async_engine

    asyncio.run(_preparer_postgres())
    url = _url_app(PG_URL)
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", url)
    get_settings.cache_clear()

    async def as_the_application(work: Any) -> Any:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                # The test itself sees every organisation; what is under test is the migration.
                await connection.execute(text("SELECT set_config('app.current_orgs', '*', true)"))
                return await connection.run_sync(work)
        finally:
            await engine.dispose()

    try:
        command.upgrade(_config(url), LEFT)
        slug = asyncio.run(as_the_application(_left_behind))
        command.upgrade(_config(url), REPLAY)
        scopes, author, hosts = asyncio.run(as_the_application(lambda c: _state(c, slug)))
    finally:
        get_settings.cache_clear()
    _check(scopes, author, hosts, slug)


_DATA = re.compile(r"\b(UPDATE|INSERT INTO|DELETE FROM)\b|bulk_insert\(|\._seed\(", re.IGNORECASE)


def test_every_data_migration_sees_the_rows_under_forced_rls() -> None:
    """A migration that writes rows must set `app.current_orgs = '*'` (or replay one that does)."""
    missing = []
    for path in sorted(VERSIONS.glob("2*.py")):
        if path.name[:8] < FIRST_GUARDED:
            continue
        source = path.read_text(encoding="utf-8")
        body = source.split('"""', 2)[-1]  # the docstring may quote the statements it explains
        if _DATA.search(body) and "set_config('app.current_orgs', '*', true)" not in body:
            missing.append(path.name)
    assert not missing, f"data migrations without the RLS bypass: {missing}"
