"""The migration that creates `org_egress_rules` seeds what the organisation already calls, so that
enforcing the list breaks nothing on upgrade (S39-22a, ADR 0066)."""

from __future__ import annotations

import json
import pathlib

import pytest
from alembic import command
from alembic.config import Config
from choregos_api.config import get_settings
from sqlalchemy import create_engine, text

API = pathlib.Path(__file__).resolve().parents[1]
BEFORE = "a4c6e8b0d2f4"


def _config(url: str) -> Config:
    config = Config(str(API / "alembic.ini"))
    config.set_main_option("script_location", str(API / "src" / "choregos_api" / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _row(connection: object, table: str, **columns: object) -> None:
    """One row at the revision before the migration, timestamps included."""
    names = [*columns, "created_at", "updated_at"]
    values = [f":{c}" for c in columns] + ["CURRENT_TIMESTAMP"] * 2
    sql = f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join(values)})"
    connection.execute(text(sql), columns)  # type: ignore[attr-defined]


def test_existing_hosts_are_seeded_and_the_table_goes_away_on_downgrade(
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
            _row(connection, "organizations", id="o1", slug="varga", name="V", settings="{}")
            skill_sources = {"skill_sources": ["gitlab.com/group", "github.com/obra"]}
            _row(
                connection,
                "organizations",
                id="o2",
                slug="other",
                name="O",
                settings=json.dumps(skill_sources),
            )
            _row(
                connection,
                "org_connectors",
                id="c1",
                org_id="o1",
                name="vendor",
                kind="mcp",
                type="mcp",
                status="unknown",
                config=json.dumps({"url": "https://mcp.x.test:8443/mcp"}),
            )
            _row(
                connection,
                "org_connectors",
                id="c2",
                org_id="o1",
                name="kb",
                kind="documents",
                type="sharepoint",
                status="unknown",
                config=json.dumps({"graph_url": "https://graph.microsoft.com/v1.0"}),
            )
            _row(
                connection,
                "knowledge_bases",
                id="kb1",
                org_id="o1",
                slug="docs",
                name="Docs",
                serving_mode="index",
                scope="org",
                projects="[]",
                groups="[]",
                eval_set="[]",
                status="active",
            )
            _row(
                connection,
                "knowledge_sources",
                id="s1",
                org_id="o1",
                knowledge_base_id="kb1",
                kind="web",
                name="Site",
                interval_minutes=60,
                status="idle",
                config=json.dumps({"url": "https://docs.x.test/guide/"}),
            )
        command.upgrade(_config(url), "d4f6a8c0e2b4")
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT o.slug, r.host, r.ports, r.used_by, r.status, r.origin, r.decided_by "
                    "FROM org_egress_rules r JOIN organizations o ON o.id = r.org_id ORDER BY o.slug, r.host"
                )
            ).all()
        command.downgrade(_config(url), BEFORE)
        with engine.connect() as connection:
            tables = {
                r[0] for r in connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            }
        command.upgrade(_config(url), "head")
    finally:
        get_settings.cache_clear()

    seeded = {
        (slug, host): (json.loads(ports), json.loads(used_by)) for slug, host, ports, used_by, *_ in rows
    }
    assert seeded[("varga", "mcp.x.test")] == ([8443], ["api", "workers"])
    assert seeded[("varga", "graph.microsoft.com")] == ([443], ["api", "workers"])
    # a SharePoint connector also opens where a download is redirected to
    assert seeded[("varga", "login.microsoftonline.com")] == ([443], ["api", "workers"])
    assert seeded[("varga", "*.sharepoint.com")] == ([443], ["api", "workers"])
    assert seeded[("varga", "docs.x.test")] == ([443], ["api", "workers"])
    # the default skill source is github.com/anthropics/skills: GitHub's hosts are open
    assert {h for (s, h) in seeded if s == "varga"} >= {"github.com", "api.github.com", "codeload.github.com"}
    # an organisation's own list decides: gitlab and github both appear for `other`
    assert {h for (s, h) in seeded if s == "other"} == {
        "gitlab.com",
        "github.com",
        "api.github.com",
        "codeload.github.com",
    }
    assert {(r[4], r[5], r[6]) for r in rows} == {("allowed", "migration", "platform")}
    assert "org_egress_rules" not in tables
