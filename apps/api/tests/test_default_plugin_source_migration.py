"""The default plugin source's new name (ADR 0186): `varga` becomes `tranibor` in `org_plugins`.

Idempotent and reversible; the unique key (organisation, plugin) means a rename never collides. Played on
SQLite here.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from tranibor_api.config import get_settings

API = pathlib.Path(__file__).resolve().parents[1]
BEFORE = "c8a1e3b5d7f9"
AFTER = "e7b9d1f3a5c8"

COLUMNS = (
    "id, org_id, name, source, version, digest, manifest, contents, details, status, is_default, installed_at"
)
SOURCES = "SELECT id, source FROM org_plugins ORDER BY id"


def _config(url: str) -> Config:
    config = Config(str(API / "alembic.ini"))
    config.set_main_option("script_location", str(API / "src" / "tranibor_api" / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _plugin(ident: str, org: str, name: str, source: str) -> dict[str, Any]:
    return {
        "id": ident, "org_id": org, "name": name, "source": source, "version": "1.0.0",
        "digest": "sha256:x", "manifest": "{}", "contents": "{}", "details": "{}",
        "status": "installed", "is_default": False, "installed_at": "2026-11-18 09:00:00",
    }  # fmt: skip


def test_the_source_is_renamed_idempotently_and_back(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "source.db"
    monkeypatch.setenv("TRANIBOR_DATABASE_URL", f"sqlite+aiosqlite:///{database}")
    get_settings.cache_clear()
    url = f"sqlite:///{database}"
    engine = create_engine(url)
    rows = [
        _plugin("p1", "o1", "netops", "varga"),
        _plugin("p2", "o1", "authoring", "varga"),
        _plugin("p3", "o1", "authoring-x", "other"),
        _plugin("p4", "o2", "netops", "tranibor"),
    ]
    try:
        command.upgrade(_config(url), BEFORE)
        with engine.begin() as connection:
            for org in ("o1", "o2"):
                connection.execute(
                    text("INSERT INTO organizations (id, slug, name, settings) VALUES (:i, :i, :i, '{}')"),
                    {"i": org},
                )
            for row in rows:
                connection.execute(
                    text(f"INSERT INTO org_plugins ({COLUMNS}) VALUES ({', '.join(':' + k for k in row)})"),
                    row,
                )
        command.upgrade(_config(url), AFTER)
        with engine.connect() as connection:
            after = [tuple(r) for r in connection.execute(text(SOURCES)).all()]
        assert after == [
            ("p1", "tranibor"), ("p2", "tranibor"), ("p3", "other"), ("p4", "tranibor"),
        ]  # fmt: skip

        command.upgrade(_config(url), "head")  # idempotent: nothing left to rename
        command.downgrade(_config(url), BEFORE)
        with engine.connect() as connection:
            back = [tuple(r) for r in connection.execute(text(SOURCES)).all()]
        assert back == [
            ("p1", "varga"), ("p2", "varga"), ("p3", "other"), ("p4", "varga"),
        ]  # fmt: skip
    finally:
        engine.dispose()
        get_settings.cache_clear()
