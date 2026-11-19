"""The ontology leaves (ADR 0181, O4-1): its tables are dropped where they exist, its branch forgotten.

Three databases are upgraded to the core's head, on SQLite:

- one that never enabled the trial (the only one 0.27.x installations have, the trial being off by
  default): nothing to drop, and nothing in `alembic_version` but the core;
- one that applied the ontology's branch to its end (`onto0003`) and moved on with the core, so that
  `alembic_version` holds two rows: the tables go, the branch's row goes, and the actions the trial
  persisted stay as they were;
- one that stopped right after the branch (`onto0002`, the core at the revision the branch depended on):
  its single row also stood for the core's position, which is written back in its place, or Alembic
  would replay the core from the start.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, inspect, text
from tranibor_api.config import get_settings
from tranibor_api.migrate import RETIRED_REVISIONS, forget_retired_revisions

API = pathlib.Path(__file__).resolve().parents[1]
BEFORE = "f3c5a7e9b1d2"
DROP = "b4d6f8a0c2e5"
ONTOLOGY_TABLES = {"ontology_versions", "managed_objects", "action_proposals"}


def _config(url: str) -> Config:
    config = Config(str(API / "alembic.ini"))
    config.set_main_option("script_location", str(API / "src" / "tranibor_api" / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture
def database(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, Engine]]:
    file = tmp_path / "ontology.db"
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{file}")
    get_settings.cache_clear()
    engine = create_engine(f"sqlite:///{file}")
    try:
        yield f"sqlite:///{file}", engine
    finally:
        engine.dispose()
        get_settings.cache_clear()


def _versions(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        return {str(r[0]) for r in connection.execute(text("SELECT version_num FROM alembic_version"))}


def _tables(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def _ontology_tables(engine: Engine, *, proposals: bool) -> None:
    """What `onto0001` (and `onto0002`) created, enough of it for the drop to be seen."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE ontology_versions (id VARCHAR(36) PRIMARY KEY, "
                "project_id VARCHAR(36) NOT NULL REFERENCES projects(id) ON DELETE CASCADE, "
                "name VARCHAR(63) NOT NULL, compiled_ir JSON NOT NULL)"
            )
        )
        connection.execute(
            text("CREATE INDEX ix_ontology_versions_project_id ON ontology_versions (project_id)")
        )
        connection.execute(
            text(
                "CREATE TABLE managed_objects (project_id VARCHAR(36) NOT NULL, "
                "object_type VARCHAR(63) NOT NULL, id VARCHAR(255) NOT NULL, properties JSON NOT NULL, "
                "PRIMARY KEY (project_id, object_type, id))"
            )
        )
        if proposals:
            connection.execute(
                text(
                    "CREATE TABLE action_proposals (id VARCHAR(36) PRIMARY KEY, "
                    "version_id VARCHAR(36) NOT NULL REFERENCES ontology_versions(id) ON DELETE CASCADE)"
                )
            )


def _set_versions(engine: Engine, *rows: str) -> None:
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM alembic_version"))
        for row in rows:
            connection.execute(text("INSERT INTO alembic_version (version_num) VALUES (:r)"), {"r": row})


def _head(url: str) -> str:
    (head,) = ScriptDirectory.from_config(_config(url)).get_heads()
    return head


def test_a_database_that_never_enabled_the_trial_upgrades_untouched(database: tuple[str, Engine]) -> None:
    url, engine = database
    command.upgrade(_config(url), BEFORE)
    before = _tables(engine)

    command.upgrade(_config(url), "head")

    assert not ONTOLOGY_TABLES & _tables(engine)
    assert _tables(engine) >= before
    assert _versions(engine) == {_head(url)}


def test_a_database_that_applied_the_branch_loses_its_tables_and_keeps_its_actions(
    database: tuple[str, Engine],
) -> None:
    url, engine = database
    command.upgrade(_config(url), BEFORE)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO organizations (id, slug, name, settings) VALUES ('o', 'o', 'o', '{}')")
        )
        connection.execute(
            text(
                "INSERT INTO projects (id, org_id, slug, name, config, status, provision_state) "
                "VALUES ('p', 'o', 'p', 'p', '{}', 'active', '{}')"
            )
        )
        # An action the ontology persisted (onto0003, then S45-39): its origin, kinds and key are frozen.
        connection.execute(
            text(
                "INSERT INTO actions (id, org_id, project_id, origin, kind, title, params, effects, "
                "proposed_by, approval, decisions, status, result, created_at, updated_at) VALUES ('a', 'o', "
                "'p', 'ontology', 'ontology.open_infra_pr', 'open_infra_pr on host-1', :params, :effects, "
                "'{}', '{}', '[]', 'succeeded', '{}', '2026-10-06 00:30:00', '2026-10-06 00:30:00')"
            ),
            {
                "params": json.dumps(
                    {"ontologie": {"action_type": "open_infra_pr"}}
                ),  # lang: fr-ok - persisted
                "effects": json.dumps(
                    [
                        {"effect": "ontology.effet", "with": {"index": 0}},
                        {"effect": "ontology.preuve"},
                    ]  # lang: fr-ok
                ),
            },
        )
    _ontology_tables(engine, proposals=False)
    # The branch depended on older core revisions, so Alembic kept the core's row beside its own.
    _set_versions(engine, BEFORE, "onto0003")

    command.upgrade(_config(url), "head")

    assert not ONTOLOGY_TABLES & _tables(engine)
    assert _versions(engine) == {_head(url)}
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT origin, kind, params, effects FROM actions WHERE id = 'a'")
        ).one()
    assert (row.origin, row.kind) == ("ontology", "ontology.open_infra_pr")
    assert json.loads(row.params) == {"ontologie": {"action_type": "open_infra_pr"}}  # lang: fr-ok
    assert [e["effect"] for e in json.loads(row.effects)] == [
        "ontology.effet",
        "ontology.preuve",
    ]  # lang: fr-ok


def test_a_single_row_of_the_branch_gives_the_core_its_position_back(database: tuple[str, Engine]) -> None:
    url, engine = database
    stood_on = RETIRED_REVISIONS["onto0002"]
    command.upgrade(_config(url), stood_on)
    _ontology_tables(engine, proposals=True)
    # `onto0002` depends on the core's `stood_on`: Alembic kept that single row, which covered both.
    _set_versions(engine, "onto0002")

    command.upgrade(_config(url), "head")

    assert not ONTOLOGY_TABLES & _tables(engine)
    assert _versions(engine) == {_head(url)}


def test_forgetting_writes_back_only_what_no_row_left_covers(database: tuple[str, Engine]) -> None:
    url, engine = database
    script = ScriptDirectory.from_config(_config(url))
    command.upgrade(_config(url), BEFORE)

    _set_versions(engine, BEFORE, "onto0003")
    with engine.begin() as connection:
        assert forget_retired_revisions(connection, script) == ["onto0003"]
    assert _versions(engine) == {BEFORE}, "the core's row already covers the branch's dependency"

    _set_versions(engine, "onto0003")
    with engine.begin() as connection:
        assert forget_retired_revisions(connection, script) == ["onto0003"]
    assert _versions(engine) == {RETIRED_REVISIONS["onto0003"]}

    with engine.begin() as connection:
        assert forget_retired_revisions(connection, script) == [], "a second pass finds nothing"
    assert _versions(engine) == {RETIRED_REVISIONS["onto0003"]}
