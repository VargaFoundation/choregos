"""The migration that gives every existing agent a scope (S39-09, ADR 0061).

An agent installed from the built-in catalogue — its version 1 authored `catalogue:<slug>@<v>` —
becomes a common (`platform`) agent; every other one an `org` agent. No agent gets a project, no
fork, and the CHECK constraint holds on both sides of the migration.
"""

from __future__ import annotations

import pathlib

import pytest
from alembic import command
from alembic.config import Config
from choregos_api.config import get_settings
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

API = pathlib.Path(__file__).resolve().parents[1]
BEFORE = "e7a9c1b3d5f7"
AFTER = "5b7d9f1a3c4e"


def _config(url: str) -> Config:
    config = Config(str(API / "alembic.ini"))
    config.set_main_option("script_location", str(API / "src" / "choregos_api" / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _agent(
    connection: object, ident: str, slug: str, first_author: str | None, later_author: str | None
) -> None:
    run = connection.execute  # type: ignore[attr-defined]
    run(
        text(
            "INSERT INTO agents (id, org_id, slug, kind, display_name, status, lifecycle) "
            "VALUES (:id, 'org', :slug, 'internal', :slug, 'active', 'active')"
        ),
        {"id": ident, "slug": slug},
    )
    for version, author in ((1, first_author), (2, later_author)):
        if version == 2 and later_author is None:
            continue
        run(
            text(
                "INSERT INTO agent_versions (id, agent_id, org_id, version, spec, checksum, created_by) "
                "VALUES (:id, :agent, 'org', :version, '{}', 'sha256:x', :author)"
            ),
            {"id": f"{ident}-v{version}", "agent": ident, "version": version, "author": author},
        )


def test_the_migration_sorts_catalogue_agents_from_the_organisations_own(
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
            _agent(connection, "a-catalogue", "developer", "catalogue:developer@1", "alice@varga.dev")
            _agent(connection, "a-written", "onboarding", "alice@varga.dev", None)
            # a template's agent is the organisation's, whatever its author
            _agent(connection, "a-template", "reviewer", "template:software-delivery", None)
            # `catalogue:` on a LATER version only: the organisation's own agent
            _agent(connection, "a-late", "planner", "bob@varga.dev", "catalogue:planner@2")
        command.upgrade(_config(url), "head")
        with engine.connect() as connection:
            rows = {
                row.slug: row
                for row in connection.execute(
                    text("SELECT slug, scope, project_id, forked_from_agent_id FROM agents")
                ).all()
            }
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text("UPDATE agents SET scope = 'project' WHERE slug = 'developer'"))
        command.downgrade(_config(url), BEFORE)
        with engine.connect() as connection:
            columns = {row[1] for row in connection.execute(text("PRAGMA table_info(agents)")).all()}
        command.upgrade(_config(url), "head")
    finally:
        get_settings.cache_clear()

    assert {slug: row.scope for slug, row in rows.items()} == {
        "developer": "platform",
        "onboarding": "org",
        "reviewer": "org",
        "planner": "org",
    }
    assert all(row.project_id is None and row.forked_from_agent_id is None for row in rows.values())
    assert not {"scope", "project_id", "forked_from_agent_id", "forked_from_version"} & columns
    assert AFTER  # the revision the tests above are written for
