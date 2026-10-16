"""The migration of named memories on a database that already holds facts (S40-11a, ADR 0075)."""

from __future__ import annotations

import json
import pathlib
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from choregos_api.config import get_settings
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

API = pathlib.Path(__file__).resolve().parents[1]
BEFORE = "e6b8d0f2a4c5"
ORG_SQL = "INSERT INTO organizations (id, slug, name, settings) VALUES ('o1', 'varga', 'Varga', '{}')"


def _config(url: str) -> Config:
    config = Config(str(API / "alembic.ini"))
    config.set_main_option("script_location", str(API / "src" / "choregos_api" / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture
def database(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    file = tmp_path / "memories.db"
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{file}")
    get_settings.cache_clear()
    yield f"sqlite:///{file}"
    get_settings.cache_clear()


def _seed_old_facts(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as db:
        db.execute(
            text("INSERT INTO organizations (id, slug, name, settings) VALUES ('o1', 'varga', 'Varga', '{}')")
        )
        db.execute(
            text(
                "INSERT INTO projects (id, org_id, slug, name, config, status, provision_state, "
                "workflow_routing) "
                "VALUES ('p1', 'o1', 'billing', 'Billing', '{}', 'active', '{}', '[]')"
            )
        )
        for fact_id, agent in (("f1", None), ("f2", "architect")):
            db.execute(
                text(
                    "INSERT INTO memory_facts (id, project_id, kind, subject, content, status, provenance, "
                    "paths, "
                    "agent_slug) VALUES (:id, 'p1', 'decision', 's', 'c', 'active', :prov, :paths, :agent)"
                ),
                {"id": fact_id, "prov": json.dumps({}), "paths": json.dumps([]), "agent": agent},
            )


def test_existing_facts_get_their_organisation_and_stay_readable(database: str) -> None:
    config = _config(database)
    command.upgrade(config, BEFORE)
    _seed_old_facts(database)
    command.upgrade(config, "head")
    engine = create_engine(database)
    with engine.connect() as db:
        rows = db.execute(
            text("SELECT id, org_id, project_id, memory_store_id, agent_slug FROM memory_facts ORDER BY id")
        ).all()
    assert rows == [("f1", "o1", "p1", None, None), ("f2", "o1", "p1", None, "architect")]


def test_a_fact_needs_a_home(database: str) -> None:
    config = _config(database)
    command.upgrade(config, "head")
    engine = create_engine(database)
    with engine.begin() as db:
        db.execute(
            text("INSERT INTO organizations (id, slug, name, settings) VALUES ('o1', 'varga', 'V', '{}')")
        )
    with pytest.raises(IntegrityError, match=r"ck_memory_facts_has_a_home|CHECK"), engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO memory_facts (id, org_id, kind, subject, content, status, provenance, paths) "
                "VALUES ('x', 'o1', 'decision', 's', 'c', 'active', '{}', '[]')"
            )
        )


def test_downgrade_refuses_while_a_global_memory_holds_facts(database: str) -> None:
    config = _config(database)
    command.upgrade(config, "head")
    engine = create_engine(database)
    with engine.begin() as db:
        db.execute(
            text("INSERT INTO organizations (id, slug, name, settings) VALUES ('o1', 'varga', 'V', '{}')")
        )
        db.execute(
            text(
                "INSERT INTO memory_stores (id, org_id, slug, display_name, scope, owner_key, reach, "
                "projects, status) VALUES ('s1', 'o1', 'lessons', 'L', 'global', 'org', 'org', '[]', "
                "'active')"
            )
        )
        db.execute(
            text(
                "INSERT INTO memory_facts (id, org_id, memory_store_id, kind, subject, content, status, "
                "provenance, paths) VALUES ('g1', 'o1', 's1', 'decision', 's', 'c', 'active', '{}', '[]')"
            )
        )
    with pytest.raises(RuntimeError, match="belong to a global memory and to no project"):
        command.downgrade(config, BEFORE)
    with engine.begin() as db:
        db.execute(text("DELETE FROM memory_facts WHERE id = 'g1'"))
    command.downgrade(config, BEFORE)
    with engine.connect() as db:
        columns = {row[1] for row in db.execute(text("PRAGMA table_info(memory_facts)"))}
        tables = {row[0] for row in db.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
    assert "org_id" not in columns and "memory_store_id" not in columns and "memory_stores" not in tables
