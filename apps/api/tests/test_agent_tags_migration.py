"""The migration that adds agent tags (S40-10): common agents get the catalogue's tags, others none."""

from __future__ import annotations

import json
import pathlib

import pytest
from alembic import command
from alembic.config import Config
from choregos_api.config import get_settings
from sqlalchemy import create_engine, text

API = pathlib.Path(__file__).resolve().parents[1]
BEFORE = "b3d5f7a9c1e4"


def _config(url: str) -> Config:
    config = Config(str(API / "alembic.ini"))
    config.set_main_option("script_location", str(API / "src" / "choregos_api" / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_the_migration_backfills_common_agents_and_leaves_the_others_empty(
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
            for ident, slug, scope in (
                ("a1", "architect", "platform"),
                ("a2", "architect-mine", "org"),
                # same slug as a catalogue agent, but the organisation's own: untouched
                ("a3", "reviewer", "org"),
            ):
                connection.execute(
                    text(
                        "INSERT INTO agents (id, org_id, slug, kind, display_name, status, lifecycle, scope) "
                        "VALUES (:id, 'org', :slug, 'internal', :slug, 'active', 'active', :scope)"
                    ),
                    {"id": ident, "slug": slug, "scope": scope},
                )
        command.upgrade(_config(url), "d4f6a8c0e2b3")
        with engine.connect() as connection:
            rows = {r.slug: r.tags for r in connection.execute(text("SELECT slug, tags FROM agents")).all()}
        command.downgrade(_config(url), BEFORE)
        with engine.connect() as connection:
            columns = {row[1] for row in connection.execute(text("PRAGMA table_info(agents)")).all()}
        command.upgrade(_config(url), "head")
    finally:
        get_settings.cache_clear()

    decoded = {slug: (json.loads(tags) if isinstance(tags, str) else tags) for slug, tags in rows.items()}
    assert decoded == {"architect": ["madr", "architecture", "design"], "architect-mine": [], "reviewer": []}
    assert "tags" not in columns
