"""Services never import routers: a router is the HTTP layer above them (ADR 0099)."""

from __future__ import annotations

import ast
from pathlib import Path

from .layering_kit import API_PACKAGE, resolve

# A hard rule: no module outside routers/ (and main.py) imports a router. S47-08 removed the last one.
CEILING = 0


def _is_router(module: str, routers: str) -> bool:
    return module == routers or module.startswith(routers + ".")


def router_imports(path: Path, package_dir: Path, package: str) -> int:
    """The router import statements of one file: one per `from`, one per aliased `import`."""
    routers = f"{package}.routers"
    count = 0
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            count += sum(1 for alias in node.names if _is_router(alias.name, routers))
        elif isinstance(node, ast.ImportFrom):
            module = resolve(path, package_dir, package, node)
            if module is None:
                continue
            if _is_router(module, routers) or (
                module == package and any(alias.name == "routers" for alias in node.names)
            ):
                count += 1
    return count


def count_router_imports(package_dir: Path, package: str) -> int:
    """Router import statements outside routers/ (and other than main.py), resolved."""
    total = 0
    for path in sorted(package_dir.rglob("*.py")):
        relative = path.relative_to(package_dir)
        if relative.parts[0] == "routers" or relative.parts == ("main.py",):
            continue
        total += router_imports(path, package_dir, package)
    return total


def test_services_do_not_import_routers() -> None:
    count = count_router_imports(API_PACKAGE, "choregos_api")
    assert count <= CEILING, (
        f"{count} router import statements outside routers/: services never import routers "
        "(ADR 0099: share helpers through services or core)"
    )


def test_the_detector_counts_every_form_by_statement(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    (package / "services").mkdir(parents=True)
    (package / "routers").mkdir()
    (package / "routers" / "x.py").write_text("from ..services.a import z\n", encoding="utf-8")
    forms = {
        "relative_from.py": ("from ..routers.x import y\n", 1),
        "absolute_from.py": ("from pkg.routers.x import y\n", 1),
        "plain_import.py": ("import pkg.routers.x\n", 1),
        "aliased_import.py": ("import pkg.routers.x as rx\n", 1),
        "package_root.py": ("from pkg import routers\n", 1),
        "relative_root.py": ("from .. import routers\n", 1),
        "relative_root_alias.py": ("from .. import routers as r\n", 1),
        "two_statements.py": ("from ..routers.x import a\nfrom ..routers.y import b\n", 2),
        "two_aliases.py": ("import pkg.routers.x, pkg.routers.y\n", 2),
        "clean.py": ("from .a import y\nfrom .. import config\nimport os\n", 0),
    }
    for name, (source, _) in forms.items():
        (package / "services" / name).write_text(source, encoding="utf-8")
    for name, (_, expected) in forms.items():
        assert router_imports(package / "services" / name, package, "pkg") == expected, name
    assert count_router_imports(package, "pkg") == sum(n for _, n in forms.values())


def test_the_detector_resolves_relative_imports(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    (package / "services").mkdir(parents=True)
    (package / "routers").mkdir()
    (package / "services" / "a.py").write_text("from ..routers.x import y\n", encoding="utf-8")
    (package / "services" / "b.py").write_text("from .a import y\n", encoding="utf-8")
    (package / "routers" / "x.py").write_text("from ..services.a import z\n", encoding="utf-8")
    assert count_router_imports(package, "pkg") == 1
