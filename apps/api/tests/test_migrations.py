"""Do the migrations say the same thing as the models?

This test exists because of a real defect: `runs.spend_collected` lived in the model and in no
migration. Nothing said so, because the API laid the schema itself at start-up (`create_all`) and
the cluster bench ran alembic with `|| true`. The defect only showed on a really migrated
database, at the first status comment written.
"""

from __future__ import annotations

import pathlib

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from choregos_api.config import get_settings
from choregos_api.db.models import Base, is_core_object
from sqlalchemy import create_engine

API = pathlib.Path(__file__).resolve().parents[1]


def _config(url: str) -> Config:
    """`migrations/env.py` takes the URL from the SETTINGS, not from the alembic config (which
    avoids repeating the connection string in two places). The test therefore follows the same
    path: it sets the environment variable and empties the settings cache."""
    config = Config(str(API / "alembic.ini"))
    config.set_main_option("script_location", str(API / "src" / "choregos_api" / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_the_migrations_produce_the_schema_of_the_models(tmp_path: pathlib.Path) -> None:
    """A migrated database must be indistinguishable from `Base.metadata`.

    SQLite is enough: the migrations use no PostgreSQL-specific type. What is compared is the
    tables and the columns, not the dialect details, which are filtered below.
    """
    file = tmp_path / "migrated.db"
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{file}")
    get_settings.cache_clear()
    try:
        command.upgrade(_config(f"sqlite:///{file}"), "head")
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()

    url = f"sqlite:///{file}"

    engine = create_engine(url)
    with engine.connect() as connection:
        # The tables of an extension loaded earlier in the session are in `Base`: they are not
        # the core's, and its own migrations create them (see `is_core_object`).
        context = MigrationContext.configure(connection, opts={"include_object": is_core_object})
        differences = compare_metadata(context, Base.metadata)

    interesting = [
        difference
        for difference in differences
        # `modify_type` and `modify_default` on SQLite speak of the dialect, not of the schema:
        # a missing or extra column, on the other hand, is a real disagreement.
        if not (isinstance(difference, tuple) and str(difference[0]).startswith("modify_"))
    ]
    if interesting:
        detail = "\n".join(f"  - {difference}" for difference in interesting)
        pytest.fail(
            "the migrations and the models do not say the same thing:\n"
            f"{detail}\n\n"
            "Generate the missing migration:\n"
            "  cd apps/api && alembic revision --autogenerate -m '…'"
        )


def test_the_migrations_go_back_down_and_up(tmp_path: pathlib.Path) -> None:
    """`downgrade base` then `upgrade head`: no migration had ever been brought back down."""
    file = tmp_path / "round-trip.db"
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{file}")
    get_settings.cache_clear()
    try:
        config = _config(f"sqlite:///{file}")
        command.upgrade(config, "head")
        command.downgrade(config, "base")
        engine = create_engine(f"sqlite:///{file}")
        with engine.connect() as connection:
            tables = connection.exec_driver_sql(
                "select name from sqlite_master where type='table'"
            ).fetchall()
        assert {t[0] for t in tables} <= {"alembic_version"}, "everything must have gone back down"
        command.upgrade(config, "head")
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"include_object": is_core_object})
            differences = compare_metadata(context, Base.metadata)
        assert not [d for d in differences if not (isinstance(d, tuple) and str(d[0]).startswith("modify_"))]
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
