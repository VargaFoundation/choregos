"""`runs.decided_at` and its backfill (S22-49, #552).

A restarted interpreter reads back a succeeded run whose decision was never applied, and counts any
other finished run as spent. The migration adds the column and marks decided every run that is over
— except the last attempt of a transition, succeeded, on a work item still in that transition's
source state: the one a restart reads back today, kept as it is. Checked on SQLite and, when a
PostgreSQL is there, as the application role under forced RLS (S39-27), where a missing bypass
would mark nothing and still end green.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from choregos_api.config import get_settings
from sqlalchemy import create_engine, text

from .conftest import PG_URL, _preparer_postgres, _url_app, sans_postgres

API = pathlib.Path(__file__).resolve().parents[1]
BEFORE = "a7c9e1b3d5f8"
DECIDED_AT = "c5e7a9b1d3f6"


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


def _run(connection: Any, item: str, transition: str, attempt: int, status: str, source: str) -> None:
    stage_input = {"transition": {"id": transition, "from": source, "to": "x", "role": "r"}}
    _row(
        connection,
        "runs",
        id=f"{item}-{transition}-{attempt}",
        work_item_id=item,
        project_id="p1",
        transition_id=transition,
        stage_role="r",
        attempt=attempt,
        status=status,
        tokens="{}",
        cost_usd=0.0,
        allowed_paths="[]",
        stage_input=json.dumps(stage_input),
    )


def _before(connection: Any) -> None:
    """Two work items as a restart would find them."""
    _row(connection, "organizations", id="o1", slug="varga", name="V", settings="{}")
    _row(
        connection,
        "projects",
        id="p1",
        org_id="o1",
        slug="dev",
        name="Dev",
        config="{}",
        status="active",
        provision_state="{}",
    )
    for item, state in (("w1", "implemented"), ("w2", "live")):
        _row(
            connection,
            "work_items",
            id=item,
            project_id="p1",
            tracker_key=f"k#{item}",
            title=item,
            state=state,
            paused=False,
            allowed_paths="[]",
            totals="{}",
            documents="{}",
        )
    # w1 came back to `implemented` after a review asked for changes: its earlier passes are decided.
    _run(connection, "w1", "t-test", 1, "succeeded", "implemented")
    _run(connection, "w1", "t-review", 1, "succeeded", "tested")
    _run(connection, "w1", "t-address-review", 1, "succeeded", "addressing_review")
    # w2: an attempt failed, the next succeeded and its interpreter died before the decision; and a
    # run still running, which a restart awaits.
    _run(connection, "w2", "t-verify-prod", 1, "failed", "live")
    _run(connection, "w2", "t-verify-prod", 2, "succeeded", "live")
    _run(connection, "w2", "t-other", 1, "running", "live")


def _decided(connection: Any) -> dict[str, bool]:
    rows = connection.execute(text("SELECT id, decided_at FROM runs"))
    return {str(run_id): decided_at is not None for run_id, decided_at in rows}


EXPECTED = {
    # Not the last pass of a transition whose work item stands in its source state: decided.
    "w1-t-review-1": True,
    "w1-t-address-review-1": True,
    # The last succeeded attempt on a work item still in its source state: what a restart reads
    # back today (#550) — kept undecided, as the table cannot tell which case it is.
    "w1-t-test-1": False,
    "w2-t-verify-prod-1": True,
    "w2-t-verify-prod-2": False,
    # Not over: a restart awaits it.
    "w2-t-other-1": False,
}


def test_the_migration_adds_decided_at_and_backfills_the_runs_on_sqlite(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "runs.db"
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{database}")
    get_settings.cache_clear()
    url = f"sqlite:///{database}"
    try:
        command.upgrade(_config(url), BEFORE)
        engine = create_engine(url)
        with engine.begin() as connection:
            _before(connection)
        command.upgrade(_config(url), DECIDED_AT)
        with engine.connect() as connection:
            decided = _decided(connection)
        command.downgrade(_config(url), BEFORE)
        with engine.connect() as connection:
            columns = {row[1] for row in connection.execute(text("PRAGMA table_info(runs)"))}
    finally:
        get_settings.cache_clear()
    assert decided == EXPECTED
    assert "decided_at" not in columns


@sans_postgres
def test_the_backfill_sees_the_runs_as_the_application_role_under_forced_rls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        command.upgrade(_config(url), BEFORE)
        asyncio.run(as_the_application(_before))
        command.upgrade(_config(url), DECIDED_AT)
        decided = asyncio.run(as_the_application(_decided))
    finally:
        get_settings.cache_clear()
    assert decided == EXPECTED
