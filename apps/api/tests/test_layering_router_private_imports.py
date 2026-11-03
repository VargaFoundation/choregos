"""Routers share helpers through services, not by importing each other's private names (ADR 0099)."""

from __future__ import annotations

import ast
from pathlib import Path

from .layering_kit import API_PACKAGE, module_of, resolve, router_unit

# S47-10 took it to zero: a router never imports another router's private name. Do not raise it;
# share the helper through a service under a public name (ADR 0099).
CEILING = 0


def count_private_router_imports(package_dir: Path, package: str) -> int:
    """ImportFrom statements in routers/**, from another router unit, naming a `_private` name.

    Imports inside one router package (`routers/agents/…`) are exempt: a split keeps them legal.
    """
    count = 0
    for path in sorted((package_dir / "routers").rglob("*.py")):
        own = router_unit(module_of(path, package_dir, package), package)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = resolve(path, package_dir, package, node)
            unit = router_unit(module, package) if module else None
            if unit is None or unit == own:
                continue
            if any(alias.name.startswith("_") for alias in node.names):
                count += 1
    return count


def test_routers_import_no_private_names_from_each_other() -> None:
    count = count_private_router_imports(API_PACKAGE, "choregos_api")
    assert count <= CEILING, (
        f"routers import private names from each other {count} times: no router may import a "
        "`_private` name from another router (ADR 0099): move the helper into a service under a public name"
    )


def test_the_detector_recurses_and_exempts_one_router_package(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    (package / "routers" / "c").mkdir(parents=True)
    (package / "routers" / "a.py").write_text("from .b import _x\n", encoding="utf-8")
    (package / "routers" / "b.py").write_text("_x = 1\n", encoding="utf-8")
    (package / "routers" / "c" / "__init__.py").write_text("from ._r import _y\n", encoding="utf-8")
    (package / "routers" / "c" / "_r.py").write_text("_y = 1\n", encoding="utf-8")
    # Recursion: a file inside a router package importing another router's private name counts.
    (package / "routers" / "c" / "deep.py").write_text("from ..b import _x\n", encoding="utf-8")
    assert count_private_router_imports(package, "pkg") == 2
