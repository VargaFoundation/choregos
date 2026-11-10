"""A SQLite database per test without laying the schema per test.

`create_all()` for the whole schema costs more than most tests do. The schema is laid once per
process into a template file; each test gets a copy. The template is keyed on the tables the
metadata holds: modules that join `Base` later (the ontology plugin) make it rebuild, so a copy is
never missing a table that `create_all()` would have laid.
"""

from __future__ import annotations

import pathlib
import shutil
import tempfile
from typing import Any

#: The template and the directory that holds it, per process.
_state: dict[str, Any] = {}


async def _build(tables: frozenset[str]) -> pathlib.Path:
    from choregos_api.config import get_settings
    from choregos_api.db.models import Base
    from choregos_api.db.session import _create_engine

    scratch = _state.get("scratch")
    if scratch is None:
        scratch = _state["scratch"] = tempfile.TemporaryDirectory(prefix="choregos-sqlite-template-")
    path = pathlib.Path(scratch.name) / f"template-{len(tables)}.db"
    path.unlink(missing_ok=True)
    # Its own engine, with the application's pragmas: the test's engine and settings stay untouched.
    settings = get_settings().model_copy(update={"database_url": f"sqlite+aiosqlite:///{path}"})
    engine = _create_engine(settings)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()
    return path


async def copy_schema_to(target: pathlib.Path) -> None:
    """Make `target` a database with the full schema, as `create_all()` leaves it."""
    from choregos_api.db.models import Base

    tables = frozenset(Base.metadata.tables)
    if _state.get("tables") != tables:
        _state["template"] = await _build(tables)
        _state["tables"] = tables
    shutil.copyfile(_state["template"], target)
