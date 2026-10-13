"""The data migration of stored core-template copies (S31-02): an untouched copy is upgraded, a
modified one is left alone with a warning naming its legacy declarations."""

from __future__ import annotations

import json
import pathlib

import pytest
from alembic import command
from alembic.config import Config
from choregos_api.config import get_settings
from choregos_core.dsl import template_yaml
from choregos_core.dsl.legacy import old_template
from sqlalchemy import create_engine, text

API = pathlib.Path(__file__).resolve().parents[1]
BEFORE = "b4d6f8a0c2e4"


def _config(url: str) -> Config:
    config = Config(str(API / "alembic.ini"))
    config.set_main_option("script_location", str(API / "src" / "choregos_api" / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _old_yaml() -> str:
    return (
        template_yaml("default-simple")
        .replace(
            "    outputs: [spec_markdown, size, risk]\n", "    outputs: [spec, allowed_paths, size, risk]\n"
        )
        .replace(
            "    inputs: [spec_markdown, allowed_paths]\n",
            "    inputs: [spec, allowed_paths]\n    outputs: [branch, commits]\n",
        )
    )


def test_untouched_copies_are_upgraded_and_modified_ones_kept(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "migrated.db"
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{database}")
    get_settings.cache_clear()
    url = f"sqlite:///{database}"
    try:
        command.upgrade(_config(url), BEFORE)
        untouched = old_template("default-simple")
        modified = old_template("default-simple")
        modified["transitions"][0]["timeout_hours"] = 12
        engine = create_engine(url)
        with engine.begin() as connection:
            for ident, document, source in (
                ("wf-old", untouched, _old_yaml()),
                ("wf-mine", modified, "custom"),
            ):
                connection.execute(
                    text(
                        "INSERT INTO workflow_defs (id, project_id, name, version, source, yaml, json, "
                        "checksum, is_active, created_at, updated_at) VALUES (:id, :project, 'default', 1, "
                        "'platform', :yaml, :json, 'sha256:old', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ),
                    {"id": ident, "project": f"p-{ident}", "yaml": source, "json": json.dumps(document)},
                )
        command.upgrade(_config(url), "head")
        # Alembic's own logging configuration writes to stderr, not to the root logger.
        logged = capfd.readouterr().err
        with engine.connect() as connection:
            rows = {
                row.id: row
                for row in connection.execute(
                    text("SELECT id, yaml, json, checksum FROM workflow_defs")
                ).all()
            }
    finally:
        get_settings.cache_clear()

    upgraded = json.loads(rows["wf-old"].json)
    refine = next(t for t in upgraded["transitions"] if t["id"] == "t-refine")
    implement = next(t for t in upgraded["transitions"] if t["id"] == "t-implement")
    assert refine["outputs"] == ["spec_markdown", "size", "risk"]
    assert implement["inputs"] == ["spec_markdown", "allowed_paths"] and not implement.get("outputs")
    assert rows["wf-old"].yaml == template_yaml("default-simple"), "the YAML keeps its comments"
    assert rows["wf-old"].checksum.startswith("sha256:") and rows["wf-old"].checksum != "sha256:old"

    kept = json.loads(rows["wf-mine"].json)
    assert kept == modified and rows["wf-mine"].yaml == "custom", "a modified copy is never rewritten"
    warnings = [line for line in logged.splitlines() if line.startswith("WARN") and "p-wf-mine" in line]
    assert len(warnings) == 1 and "t-refine: spec" in warnings[0] and "t-implement: commits" in warnings[0], (
        logged
    )
