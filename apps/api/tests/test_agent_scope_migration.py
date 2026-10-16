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
        # this revision only: the later backfill of S39-25 moves template-installed catalogue agents
        command.upgrade(_config(url), AFTER)
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


# ───────────── the backfill of the catalogue provenance of template-installed agents (S39-25) ─────────────

TEMPLATE_BEFORE = "d4f6a8c0e2b4"


def test_the_backfill_gives_template_installed_catalogue_agents_their_provenance(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from choregos_api.schemas import AgentSpec
    from choregos_api.services.agents import empreinte
    from choregos_core.catalogue_d_agents import entree

    reviewer, triager = entree("reviewer"), entree("triager")
    assert reviewer is not None and triager is not None
    spec = AgentSpec.model_validate(reviewer.document["spec"])
    database = tmp_path / "backfill.db"
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{database}")
    get_settings.cache_clear()
    url = f"sqlite:///{database}"

    def agent(connection: object, ident: str, slug: str, versions: list[tuple[str, str, str]]) -> None:
        run = connection.execute  # type: ignore[attr-defined]
        run(
            text(
                "INSERT INTO agents (id, org_id, slug, kind, display_name, status, lifecycle, scope) "
                "VALUES (:id, 'org', :slug, 'internal', :slug, 'active', 'active', 'org')"
            ),
            {"id": ident, "slug": slug},
        )
        for number, (author, checksum, body) in enumerate(versions, start=1):
            run(
                text(
                    "INSERT INTO agent_versions (id, agent_id, org_id, version, spec, checksum, created_by) "
                    "VALUES (:id, :agent, 'org', :version, :spec, :checksum, :author)"
                ),
                {"id": f"{ident}-v{number}", "agent": ident, "version": number, "spec": body,
                 "checksum": checksum, "author": author},
            )  # fmt: skip

    same = json.dumps(spec.model_dump(mode="json"))
    try:
        command.upgrade(_config(url), TEMPLATE_BEFORE)
        engine = create_engine(url)
        with engine.begin() as connection:
            # in step with the catalogue
            agent(
                connection,
                "a-sync",
                "reviewer",
                [("template:github-software-delivery@1.0.0", empreinte(spec), same)],
            )
            # a hand-published v2 on top of the catalogue's text
            agent(connection, "a-edited", "triager",
                  [("template:x@1", empreinte(AgentSpec.model_validate(triager.document["spec"])), "{}"),
                   ("alice@varga.dev", "sha256:by-hand", "{}")])  # fmt: skip
            # an agent outside the catalogue, and a catalogue name that a person wrote
            agent(connection, "a-own", "my-own", [("template:x@1", "sha256:x", "{}")])
            agent(connection, "a-written", "developer", [("alice@varga.dev", "sha256:x", "{}")])
        command.upgrade(_config(url), "head")
        command.upgrade(_config(url), "head")  # idempotent
        with engine.connect() as connection:
            rows = {
                row.slug: row
                for row in connection.execute(
                    text(
                        "SELECT a.slug, a.scope, v.created_by FROM agents a "
                        "JOIN agent_versions v ON v.agent_id = a.id AND v.version = 1"
                    )
                ).all()
            }
    finally:
        get_settings.cache_clear()

    assert rows["reviewer"].created_by == f"catalogue:reviewer@{reviewer.version}"
    assert rows["reviewer"].scope == "platform"
    assert rows["triager"].created_by == f"catalogue:triager@{triager.version}!diverged"
    assert rows["triager"].scope == "platform"
    assert (rows["my-own"].created_by, rows["my-own"].scope) == ("template:x@1", "org")
    assert (rows["developer"].created_by, rows["developer"].scope) == ("alice@varga.dev", "org")
