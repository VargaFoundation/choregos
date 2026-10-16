"""The migration that gives versions a message and a restore origin (S40-12a, ADR 0076)."""

from __future__ import annotations

import pathlib

import pytest
from alembic import command
from alembic.config import Config
from choregos_api.config import get_settings
from sqlalchemy import create_engine, text

API = pathlib.Path(__file__).resolve().parents[1]
BEFORE = "d4f6a8c0e2b3"
AFTER = "e6b8d0f2a4c5"


def _config(url: str) -> Config:
    config = Config(str(API / "alembic.ini"))
    config.set_main_option("script_location", str(API / "src" / "choregos_api" / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_the_migration_adds_nullable_columns_and_keeps_old_rows_without_a_message(
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
            connection.execute(
                text(
                    "INSERT INTO agents (id, org_id, slug, kind, display_name, status, lifecycle) "
                    "VALUES ('a1', 'org', 'old', 'internal', 'Old', 'active', 'active')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO agent_versions (id, agent_id, org_id, version, spec, checksum, created_by) "
                    "VALUES ('v1', 'a1', 'org', 1, '{}', 'sha256:x', 'alice@varga.dev')"
                )
            )
        command.upgrade(_config(url), AFTER)
        with engine.connect() as connection:
            row = connection.execute(text("SELECT message, restored_from FROM agent_versions")).one()
        command.downgrade(_config(url), BEFORE)
        with engine.connect() as connection:
            columns = {r[1] for r in connection.execute(text("PRAGMA table_info(agent_versions)")).all()}
        command.upgrade(_config(url), "head")
    finally:
        get_settings.cache_clear()

    assert (row.message, row.restored_from) == (None, None)
    assert not {"message", "restored_from"} & columns
