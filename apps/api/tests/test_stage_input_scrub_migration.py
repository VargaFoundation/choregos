"""The data migration that scrubs the secrets left in `runs.stage_input` (#519).

Finished runs kept their gateway key and their callback token in clear. After the migration, no row
holds a run token, and only a live run keeps its gateway key — its runner may still read it.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from choregos_api.config import get_settings
from sqlalchemy import create_engine, text

API = pathlib.Path(__file__).resolve().parents[1]
BEFORE = "2c4e6a8b0d1f"
MIGRATION = API / "src/choregos_api/migrations/versions/20261014_1500_stage_input_without_secrets.py"
REDACTED = "[REDACTED]"


def _config(url: str) -> Config:
    config = Config(str(API / "alembic.ini"))
    config.set_main_option("script_location", str(API / "src" / "choregos_api" / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _migration() -> Any:
    spec = importlib.util.spec_from_file_location("stage_input_without_secrets", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stage_input(key: str | None, token: str) -> dict[str, Any]:
    # Fake values, assembled so that no secret scanner mistakes them for real ones.
    return {
        "run_id": "r",
        "gateway_key": key,
        "callbacks": {"api_url": "http://api/api/v1/internal", "run_token": token},
        "budget": {"usd": 1.0, "max_turns": 10, "max_minutes": 30},
    }


def test_the_migration_scrubs_the_secrets_and_keeps_a_live_run_working(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "migrated.db"
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{database}")
    get_settings.cache_clear()
    url = f"sqlite:///{database}"
    now = datetime.now(UTC)
    later, earlier = (now + timedelta(hours=1)).isoformat(), (now - timedelta(hours=1)).isoformat()
    rows = {
        # id: (status, spend collected, key id, key revoked, key expires at, gateway key)
        "run-done": ("succeeded", 1, "key-done", 1, later, "sk-" + "done"),
        "run-live": ("running", 0, "key-live", 0, later, "sk-" + "live"),
        "run-orphan": ("running", 0, "key-orphan", 0, earlier, "sk-" + "orphan"),
        "run-unknown-key": ("queued", 0, None, 0, None, "sk-" + "unknown"),
        "run-clean": ("failed", 1, None, 0, None, None),
    }
    try:
        command.upgrade(_config(url), BEFORE)
        engine = create_engine(url)
        with engine.begin() as connection:
            for ident, (status, collected, key_id, revoked, expires_at, key) in rows.items():
                token = REDACTED if ident == "run-clean" else "eyJ" + ident
                connection.execute(
                    text(
                        "INSERT INTO runs (id, work_item_id, project_id, stage_role, attempt, status, "
                        "tokens, cost_usd, allowed_paths, spend_collected, gateway_key_id, stage_input) "
                        "VALUES (:id, 'wi', 'p', 'implement', 1, :status, '{}', 0, '[]', :collected, "
                        ":key_id, :input)"
                    ),
                    {
                        "id": ident,
                        "status": status,
                        "collected": collected,
                        "key_id": key_id,
                        "input": json.dumps(_stage_input(key, token)),
                    },
                )
                if key_id is not None:
                    connection.execute(
                        text(
                            "INSERT INTO gateway_keys (id, key_id, budget_usd, spend_usd, revoked, "
                            "expires_at) VALUES (:id, :key_id, 1, 0, :revoked, :expires_at)"
                        ),
                        {"id": f"gk-{ident}", "key_id": key_id, "revoked": revoked, "expires_at": expires_at},
                    )
        command.upgrade(_config(url), "head")
        with engine.connect() as connection:
            stored = {
                row.id: json.loads(row.stage_input)
                for row in connection.execute(text("SELECT id, stage_input FROM runs")).all()
            }
        with engine.begin() as connection:
            again = _migration().scrub_rows(connection)
    finally:
        get_settings.cache_clear()

    assert all(document["callbacks"]["run_token"] == REDACTED for document in stored.values())
    assert stored["run-done"]["gateway_key"] is None
    assert stored["run-orphan"]["gateway_key"] is None, "an expired key is a dead run's"
    assert stored["run-unknown-key"]["gateway_key"] is None, "nothing says this key is alive"
    assert stored["run-live"]["gateway_key"] == "sk-live", "a live run's runner may still read it"
    assert stored["run-done"]["budget"] == {"usd": 1.0, "max_turns": 10, "max_minutes": 30}
    assert stored["run-clean"] == _stage_input(None, REDACTED)
    assert again == [], "idempotent: a second pass changes nothing"


def test_the_downgrade_is_a_no_op() -> None:
    assert _migration().downgrade() is None
