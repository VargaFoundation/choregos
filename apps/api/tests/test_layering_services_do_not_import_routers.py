"""Services never import routers: a router is the HTTP layer above them (ADR 0099)."""

from __future__ import annotations

import ast
from pathlib import Path

from .layering_kit import API_PACKAGE, resolve

# Measured at 87ea1831d (services/readiness.py). Lower it in the PR that removes the import; raise
# it only for a mechanical rename that splits a statement, saying so in the PR body, or by amending
# ADR 0099.
CEILING = 1


def importers_of_routers(package_dir: Path, package: str) -> list[str]:
    """Modules outside routers/ (and other than main.py) that import a router, resolved."""
    routers = f"{package}.routers"
    found: list[str] = []
    for path in sorted(package_dir.rglob("*.py")):
        relative = path.relative_to(package_dir)
        if relative.parts[0] == "routers" or relative.parts == ("main.py",):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = resolve(path, package_dir, package, node)
            if module and (module == routers or module.startswith(routers + ".")):
                found.append(relative.as_posix())
                break
    return found


def test_services_do_not_import_routers() -> None:
    found = importers_of_routers(API_PACKAGE, "choregos_api")
    assert len(found) <= CEILING, (
        f"{len(found)} modules import a router ({found}), the ceiling is {CEILING}: "
        "a new import is refused (ADR 0099: share helpers through services)"
    )


def test_the_detector_resolves_relative_imports(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    (package / "services").mkdir(parents=True)
    (package / "routers").mkdir()
    (package / "services" / "a.py").write_text("from ..routers.x import y\n", encoding="utf-8")
    (package / "services" / "b.py").write_text("from .a import y\n", encoding="utf-8")
    (package / "routers" / "x.py").write_text("from ..services.a import z\n", encoding="utf-8")
    assert importers_of_routers(package, "pkg") == ["services/a.py"]
