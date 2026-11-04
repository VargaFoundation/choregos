# SPDX-License-Identifier: Apache-2.0
"""S47-31: `db/models/` is a package of domain modules, and the data model did not change.

The metadata description and the public names are pinned against fixtures captured from the
single-file `db/models.py` before it was split. Alembic compares the models to the migrations, so a
moved class must not change a table, a column, a constraint or an index.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from choregos_api.db import models
from choregos_api.db.base import Base

PACKAGE = Path(models.__file__).parent
FIXTURES = Path(__file__).parent / "fixtures"
MAX_LINES = 400


def _reprs(value: Any) -> str | None:
    """A repr without memory addresses, which differ from one process to the next."""
    return None if value is None else re.sub(r" at 0x[0-9a-f]+", "", repr(value))


def _foreign_tables() -> set[str]:
    """Tables a package outside the core maps on the core's `Base` (the ontology plugin's, once it is
    imported by an earlier test of the same run): not part of `db/models/`, so not pinned here."""
    return {
        mapper.persist_selectable.fullname  # type: ignore[attr-defined]
        for mapper in Base.registry.mappers
        if not mapper.class_.__module__.startswith(f"{models.__name__}.")
        and mapper.class_.__module__ != models.__name__
    }


def describe_metadata() -> dict[str, Any]:
    """A canonical, JSON-able description of the core's tables in `Base.metadata`, sorted by name."""
    tables: dict[str, Any] = {}
    foreign = _foreign_tables()
    for name, table in sorted(Base.metadata.tables.items()):
        if name in foreign:
            continue
        columns = [
            {
                "name": column.name,
                "type": repr(column.type),
                "nullable": column.nullable,
                "default": _reprs(column.default.arg if column.default is not None else None),
                "server_default": _reprs(
                    column.server_default.arg if column.server_default is not None else None
                ),
            }
            for column in table.columns
        ]
        foreign_keys = sorted(
            (
                sorted(element.parent.name for element in fk.elements),
                sorted(element.target_fullname for element in fk.elements),
                fk.ondelete,
                fk.onupdate,
                fk.name,
            )
            for fk in table.foreign_key_constraints
        )
        uniques = sorted(
            (constraint.name, [column.name for column in constraint.columns])
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        )
        checks = sorted(
            (constraint.name, str(constraint.sqltext))
            for constraint in table.constraints
            if constraint.__class__.__name__ == "CheckConstraint"
        )
        indexes = sorted(
            (index.name, [str(expr) for expr in index.expressions], bool(index.unique))
            for index in table.indexes
        )
        tables[name] = {
            "columns": columns,
            "primary_key": [column.name for column in table.primary_key.columns],
            "foreign_keys": [list(item) for item in foreign_keys],
            "uniques": [list(item) for item in uniques],
            "checks": [list(item) for item in checks],
            "indexes": [list(item) for item in indexes],
        }
    return tables


def test_the_metadata_is_unchanged() -> None:
    expected = json.loads((FIXTURES / "model_metadata.json").read_text(encoding="utf-8"))
    current = json.loads(json.dumps(describe_metadata()))
    assert sorted(current) == sorted(expected)
    for name in expected:
        assert current[name] == expected[name], f"table {name} changed"


def test_every_public_name_still_imports() -> None:
    names = json.loads((FIXTURES / "model_public_names.json").read_text(encoding="utf-8"))
    missing = [name for name in names if not hasattr(models, name)]
    assert not missing, f"names no longer importable from choregos_api.db.models: {missing}"
    assert set(names) <= set(models.__all__)


def test_no_model_module_exceeds_400_lines() -> None:
    assert PACKAGE.name == "models" and (PACKAGE / "__init__.py").is_file()
    too_long = {
        path.name: lines
        for path in sorted(PACKAGE.glob("*.py"))
        if (lines := len(path.read_text(encoding="utf-8").splitlines())) > MAX_LINES
    }
    assert not too_long, f"modules over {MAX_LINES} lines: {too_long}"
