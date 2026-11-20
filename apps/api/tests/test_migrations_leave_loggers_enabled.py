"""Running a migration does not switch the process's other loggers off (#2008).

`migrations/env.py` calls `logging.config.fileConfig(alembic.ini)`, whose default disables every logger
that already exists and is not named in the ini. In a test process that migrated, `temporalio.workflow`
stayed disabled and the orchestrator's `test_an_interpreter_left_over_on_a_trial_item_replays_to_its_end`
never saw its log line: it failed on whichever xdist worker had migrated before it (five CI failures in
two days).
"""

from __future__ import annotations

import logging
import pathlib

from alembic import command
from alembic.config import Config
from tranibor_api.config import get_settings

API = pathlib.Path(__file__).resolve().parents[1]
FIRST_REVISION = "a4c6e8b0d2f4"


def test_a_migration_leaves_an_existing_logger_enabled(tmp_path: pathlib.Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    database = tmp_path / "migrated.db"
    monkeypatch.setenv("TRANIBOR_DATABASE_URL", f"sqlite+aiosqlite:///{database}")
    get_settings.cache_clear()
    bystander = logging.getLogger("a.library.that.logs")
    config = Config(str(API / "alembic.ini"))
    config.set_main_option("script_location", str(API / "src" / "tranibor_api" / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    try:
        command.upgrade(config, FIRST_REVISION)
    finally:
        get_settings.cache_clear()
    assert bystander.disabled is False, "the migration's fileConfig switched the process's loggers off"
