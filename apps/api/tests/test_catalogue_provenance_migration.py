"""The migration that gives catalogue agents their plugin's provenance (S43-09, ADR 0093 §6).

`catalogue:<slug>@<rest>` becomes `plugin:<plugin>/<slug>@<rest>` (the `!diverged` mark kept), a skill a
catalogue entry installed becomes the plugin's, and every organisation that holds any of a plugin's
agents gets an `org_plugins` row for it, so the plugin's upgrade reaches them. What the organisation
wrote is never touched. Played on SQLite here; `test_migrations_postgres.py` plays it on PostgreSQL
under the migrating role's RLS.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from choregos_api.config import get_settings
from sqlalchemy import create_engine, text

API = pathlib.Path(__file__).resolve().parents[1]
BEFORE = "c9e1a3b5d7f8"
AFTER = "b8d0f2a4c6e1"

#: What an organisation held before S43-09: two catalogue agents (one it changed), its own agent, the
#: catalogue skill, and a second organisation that holds none.
SEED: tuple[tuple[str, dict[str, Any]], ...] = (
    ("organizations", {"id": "o1", "slug": "varga", "name": "Varga", "settings": "{}"}),
    ("organizations", {"id": "o2", "slug": "other", "name": "Other", "settings": "{}"}),
    *(
        (
            "agents",
            {"id": ident, "org_id": "o1", "slug": slug, "kind": "internal", "display_name": slug}
            | {"status": "active", "lifecycle": "active", "scope": scope},
        )
        for ident, slug, scope in (
            ("a-arch", "architect", "platform"),
            ("a-tri", "triager", "platform"),
            ("a-own", "my-own", "org"),
        )
    ),
    *(
        (
            "agent_versions",
            {"id": ident, "agent_id": agent, "org_id": "o1", "version": version, "spec": "{}"}
            | {"checksum": f"sha256:{ident}", "created_by": author},
        )
        for ident, agent, version, author in (
            ("v-arch-1", "a-arch", 1, "catalogue:architect@1"),
            ("v-arch-2", "a-arch", 2, "catalogue:architect@2"),
            ("v-tri-1", "a-tri", 1, "catalogue:triager@1!diverged"),
            ("v-tri-2", "a-tri", 2, "alice@varga.dev"),
            ("v-own-1", "a-own", 1, "template:x@1"),
        )
    ),
    ("skills", {"id": "s-madr", "org_id": "o1", "slug": "madr-4", "description": "MADR", "status": "active"}),
    (
        "skill_versions",
        {"id": "sv-madr-1", "skill_id": "s-madr", "org_id": "o1", "version": 1, "files": "{}"}
        | {"digest": "sha256:madr", "created_by": "catalogue:architect@2"},
    ),
)

AUTHORS = "SELECT id, created_by FROM agent_versions ORDER BY id"
SKILL_AUTHORS = "SELECT id, created_by FROM skill_versions ORDER BY id"
PLUGINS = "SELECT org_id, name, source, version, contents, details FROM org_plugins ORDER BY org_id, name"

EXPECTED_AUTHORS = [
    ("v-arch-1", "plugin:software-delivery/architect@1"),
    ("v-arch-2", "plugin:software-delivery/architect@2"),
    ("v-own-1", "template:x@1"),
    ("v-tri-1", "plugin:software-delivery/triager@1!diverged"),
    ("v-tri-2", "alice@varga.dev"),
]
BEFORE_AUTHORS = [
    ("v-arch-1", "catalogue:architect@1"),
    ("v-arch-2", "catalogue:architect@2"),
    ("v-own-1", "template:x@1"),
    ("v-tri-1", "catalogue:triager@1!diverged"),
    ("v-tri-2", "alice@varga.dev"),
]


def check_upgraded(authors: list[Any], skills: list[Any], plugins: list[Any]) -> None:
    assert [tuple(r) for r in authors] == EXPECTED_AUTHORS
    assert [tuple(r) for r in skills] == [("sv-madr-1", "plugin:software-delivery/madr-4@1.0.0")]
    assert [(r[0], r[1], r[2], r[3]) for r in plugins] == [("o1", "software-delivery", "varga", "1.0.0")]
    contents = plugins[0][4] if isinstance(plugins[0][4], dict) else json.loads(plugins[0][4])
    details = plugins[0][5] if isinstance(plugins[0][5], dict) else json.loads(plugins[0][5])
    assert contents["agents"] == ["architect", "triager"] and contents["skills"] == ["madr-4"]
    assert details["migrated_from"] == "catalogue"


def _config(url: str) -> Config:
    config = Config(str(API / "alembic.ini"))
    config.set_main_option("script_location", str(API / "src" / "choregos_api" / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_the_migration_names_the_plugin_and_records_it(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "provenance.db"
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{database}")
    get_settings.cache_clear()
    url = f"sqlite:///{database}"
    engine = create_engine(url)

    def read() -> tuple[list[Any], list[Any], list[Any]]:
        with engine.connect() as connection:
            return tuple(  # type: ignore[return-value]
                list(connection.execute(text(q)).all()) for q in (AUTHORS, SKILL_AUTHORS, PLUGINS)
            )

    try:
        command.upgrade(_config(url), BEFORE)
        with engine.begin() as connection:
            for table, row in SEED:
                connection.execute(
                    text(
                        f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join(':' + k for k in row)})"
                    ),
                    row,
                )
        command.upgrade(_config(url), AFTER)
        check_upgraded(*read())

        command.downgrade(_config(url), BEFORE)
        authors, _, plugins = read()
        assert [tuple(r) for r in authors] == BEFORE_AUTHORS and plugins == []

        command.upgrade(_config(url), "head")
        check_upgraded(*read())
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_the_mapping_is_the_offline_plugins() -> None:
    """The migration writes its mapping out; it must be what the shipped plugins hold."""
    import importlib.util

    from choregos_core.catalogue_d_agents import catalogue

    path = next(
        (API / "src" / "choregos_api" / "migrations" / "versions").glob(
            "*_catalogue_agents_become_plugins.py"
        )
    )
    spec = importlib.util.spec_from_file_location("s43_09_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    shipped = catalogue()
    for name, (version, _digest, agents, skills, _) in module.PLUGINS.items():
        assert shipped.plugins[name] == version
        assert set(agents) == {e.slug for e in shipped.entries if e.plugin == name}, name
        assert set(skills) == {s for s, found in shipped.skills.items() if found.plugin == name}, name
    from choregos_core.plugins.sources import read_lock

    locked = read_lock(pathlib.Path(__file__).resolve().parents[3] / "plugins" / "offline")
    assert locked is not None
    assert {n: (v, d) for n, (v, d, *_) in module.PLUGINS.items()} == locked
