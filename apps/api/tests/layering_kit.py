"""Shared helpers of the layering guards: resolve the imports of a file inside the API package."""

from __future__ import annotations

import ast
from pathlib import Path

API_PACKAGE = Path(__file__).resolve().parents[1] / "src" / "choregos_api"


def module_of(path: Path, package_dir: Path, package: str) -> str:
    """The dotted module of a file under `package_dir`, with `__init__` collapsed."""
    parts = [package, *path.relative_to(package_dir).with_suffix("").parts]
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def resolve(path: Path, package_dir: Path, package: str, node: ast.ImportFrom) -> str | None:
    """The absolute module an `ImportFrom` names, or None when a relative import leaves the package."""
    if node.level == 0:
        return node.module
    parts = module_of(path, package_dir, package).split(".")
    # The package of the file: the module itself for an __init__.py, its parent otherwise.
    if path.name != "__init__.py":
        parts = parts[:-1]
    drop = node.level - 1
    if drop >= len(parts):
        return None
    base = parts[: len(parts) - drop]
    if node.module:
        base = [*base, node.module]
    return ".".join(base)


def router_unit(module: str, package: str) -> str | None:
    """`X` for `<package>.routers.X` and `<package>.routers.X.anything`, otherwise None."""
    prefix = f"{package}.routers."
    if not module.startswith(prefix):
        return None
    return module[len(prefix) :].split(".", maxsplit=1)[0]
