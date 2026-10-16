"""The migration that gives every agent a production pointer (S40-02a, ADR 0071).

Every existing agent is in production at its latest version — what "latest" meant before channels —
with one `baseline` row (the rollback oracle); no pin moves; a pin is a revision XOR a channel; and
both directions of the migration work.
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
BEFORE = "b2c4d6e8f0a1"
AFTER = "d4f6a8c0e2b4"
APPROVALS = "f6b8d0a2c4e6"


def _config(url: str) -> Config:
    config = Config(str(API / "alembic.ini"))
    config.set_main_option("script_location", str(API / "src" / "choregos_api" / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _agent(connection: object, ident: str, slug: str, versions: int) -> None:
    run = connection.execute  # type: ignore[attr-defined]
    run(
        text(
            "INSERT INTO agents (id, org_id, slug, kind, display_name, status, lifecycle) "
            "VALUES (:id, 'org', :slug, 'internal', :slug, 'active', 'active')"
        ),
        {"id": ident, "slug": slug},
    )
    for version in range(1, versions + 1):
        run(
            text(
                "INSERT INTO agent_versions (id, agent_id, org_id, version, spec, checksum) "
                "VALUES (:id, :agent, 'org', :version, '{}', 'sha256:x')"
            ),
            {"id": f"{ident}-v{version}", "agent": ident, "version": version},
        )


def test_every_agent_starts_in_production_at_its_latest_version(
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
            _agent(connection, "a-three", "reviewer", 3)
            _agent(connection, "a-one", "planner", 1)
            connection.execute(
                text(
                    "INSERT INTO projects (id, org_id, slug, name, config, status, provision_state, "
                    "workflow_routing) VALUES ('p1', 'org', 'dev', 'Dev', '{}', 'active', '{}', '[]')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO project_agents (id, project_id, org_id, agent_id, version, overrides) "
                    "VALUES ('pin1', 'p1', 'org', 'a-three', 2, '{}')"
                )
            )
        command.upgrade(_config(url), "head")
        with engine.connect() as connection:
            stable = dict(connection.execute(text("SELECT slug, stable_version FROM agents")).all())
            agents = connection.execute(
                text("SELECT candidate_version, candidate_percent, release_policy FROM agents")
            ).all()
            baselines = connection.execute(
                text(
                    "SELECT channel, kind, status, requested_by, version FROM agent_releases ORDER BY version"
                )
            ).all()
            channel = connection.execute(text("SELECT agent_channel FROM projects")).scalar_one()
            pin = connection.execute(text("SELECT version, channel FROM project_agents")).one()
        assert stable == {"reviewer": 3, "planner": 1}
        assert all(row[0] is None and row[1] == 0 and row[2] is None for row in agents)
        assert [tuple(b) for b in baselines] == [
            ("stable", "baseline", "done", "migration", 1),
            ("stable", "baseline", "done", "migration", 3),
        ]
        assert channel == "stable"
        assert tuple(pin) == (2, None), "the pin is kept as a fixed revision"
        with engine.connect() as connection:
            waited = connection.execute(
                text("SELECT approvals_required, decisions FROM agent_releases")
            ).all()
        assert all(row[0] == 0 and str(row[1]) in {"[]", "[]\n"} for row in waited), "no one waited so far"

        for version, chan in ((2, "stable"), (None, None)):
            with pytest.raises(IntegrityError), engine.begin() as connection:
                connection.execute(
                    text("UPDATE project_agents SET version = :v, channel = :c"), {"v": version, "c": chan}
                )
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text("UPDATE agents SET candidate_percent = 101"))

        with engine.begin() as connection:
            connection.execute(text("UPDATE project_agents SET version = NULL, channel = 'candidate'"))
        command.downgrade(_config(url), AFTER)
        with engine.connect() as connection:
            released = {row[1] for row in connection.execute(text("PRAGMA table_info(agent_releases)")).all()}
        assert not {"approvals_required", "decisions"} & released and "channel" in released
        command.upgrade(_config(url), APPROVALS)
        command.downgrade(_config(url), BEFORE)
        with engine.connect() as connection:
            columns = {row[1] for row in connection.execute(text("PRAGMA table_info(agents)")).all()}
            pins = connection.execute(text("SELECT count(*) FROM project_agents")).scalar_one()
            tables = {
                row[0]
                for row in connection.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'"))
            }
        assert not {"stable_version", "candidate_version", "release_policy"} & columns
        assert pins == 0, "a channel pin has no revision to go back to: it is removed"
        assert "agent_releases" not in tables
        command.upgrade(_config(url), "head")
    finally:
        get_settings.cache_clear()
    assert AFTER
