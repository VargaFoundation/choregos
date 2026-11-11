# SPDX-License-Identifier: Apache-2.0
"""The migration that makes sessions resumable (S63-04): a session open at the upgrade is closed
(`reason: migrated`), a closed one is left as it was, and every existing row is a `session`."""

from __future__ import annotations

import pathlib

import pytest
from alembic import command
from alembic.config import Config
from choregos_api.config import get_settings
from sqlalchemy import create_engine, text

API = pathlib.Path(__file__).resolve().parents[1]
BEFORE = "b1d3f5a7c9e2"


def _config(url: str) -> Config:
    config = Config(str(API / "alembic.ini"))
    config.set_main_option("script_location", str(API / "src" / "choregos_api" / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_the_migration_closes_the_open_sessions_and_leaves_the_others(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "migrated.db"
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{database}")
    get_settings.cache_clear()
    url = f"sqlite:///{database}"
    try:
        command.upgrade(_config(url), BEFORE)
        engine = create_engine(url)
        with engine.begin() as connection:
            for ident, status, reason in (("s-open", "open", None), ("s-done", "closed", "closed")):
                connection.execute(
                    text(
                        "INSERT INTO agent_sessions (id, org_id, project_id, agent_slug, agent_version, "
                        "user_id, status, reason, model, messages, responses, tool_calls, cost_usd, "
                        "expires_at) VALUES (:id, 'o', 'p', 'helper', 1, 'u', :status, :reason, 'm', "
                        "'[]', 0, 0, 0, '2026-11-10 12:00:00')"
                    ),
                    {"id": ident, "status": status, "reason": reason},
                )
        command.upgrade(_config(url), "c4b6d8f0a2e3")
        with engine.connect() as connection:
            rows = {
                r.id: r
                for r in connection.execute(
                    text(
                        "SELECT id, status, reason, closed_at, kind, turns, resolved_subjects "
                        "FROM agent_sessions"
                    )
                ).all()
            }
        command.downgrade(_config(url), BEFORE)
        command.upgrade(_config(url), "head")
    finally:
        get_settings.cache_clear()

    assert (rows["s-open"].status, rows["s-open"].reason) == ("closed", "migrated")
    assert rows["s-open"].closed_at is not None
    assert (rows["s-done"].status, rows["s-done"].reason, rows["s-done"].closed_at) == (
        "closed",
        "closed",
        None,
    )
    assert {r.kind for r in rows.values()} == {"session"} and {r.turns for r in rows.values()} == {0}
    assert {r.resolved_subjects for r in rows.values()} == {"[]"}
