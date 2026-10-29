"""One organisation lookup, not a copy per router (ADR 0099)."""

from __future__ import annotations

import ast
from pathlib import Path

from .layering_kit import API_PACKAGE

# Measured at 87ea1831d. Lower it in the PR that removes helpers; raise it only by amending ADR 0099.
CEILING = 12
NAMES = ("_organisation", "_org")


def count_organisation_helpers(package_dir: Path) -> int:
    """Module-level functions named `_organisation` or `_org` in routers/**/*.py."""
    count = 0
    for path in (package_dir / "routers").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        count += sum(
            1
            for node in tree.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in NAMES
        )
    return count


def test_one_organisation_lookup() -> None:
    count = count_organisation_helpers(API_PACKAGE)
    assert count <= CEILING, (
        f"{count} organisation lookups in routers, the ceiling is {CEILING}: "
        "use services.organisations.organisation_by_slug (S47-09)"
    )


def test_the_counter_is_recursive_and_module_level(tmp_path: Path) -> None:
    routers = tmp_path / "routers"
    (routers / "pkg").mkdir(parents=True)
    (routers / "a.py").write_text("async def _organisation(): ...\n", encoding="utf-8")
    (routers / "pkg" / "b.py").write_text(
        "def _org(): ...\n\ndef other():\n    def _org(): ...\n", encoding="utf-8"
    )
    assert count_organisation_helpers(tmp_path) == 2
