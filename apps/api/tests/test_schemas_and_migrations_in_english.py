"""S45-13: the schemas and the migrations are English, and the migration graph did not move.

`tools/check_english.py` counts French prose, French declared names and French path components in
every tracked file. The first test runs its tree scan over `schemas/` and `migrations/` and refuses
any finding, so a French comment, a French local name or a French file name cannot come back without
the test turning red. The second one parses every migration (never sorts file names: the order of
the chain is not the order of the names) and checks that each revision recorded when the files were
renamed is still there with the same parent: a rename must not change a revision id, nor re-parent.
Revisions added after the recording are free to exist; they are not listed here.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import pathlib
import sys
from types import ModuleType

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
API = "apps/api/src/choregos_api/"
VERSIONS = ROOT / API / "migrations" / "versions"

#: (revision, down_revision) of every migration on main when S45-13 renamed the files (87 pairs).
RECORDED: frozenset[tuple[str, str | None]] = frozenset(
    {
        ("0df3d8ee7496", "7bdeea6732b0"),
        ("1b3d5f7a9c0e", "3d5f7b9c1e2a"),
        ("2c4e6a8b0d1f", "1b3d5f7a9c0e"),
        ("3c5e7a9b1d2f", "97b19c72c16e"),
        ("3d5f7b9c1e2a", "5f7b9d1c3e4a"),
        ("4a6c8e0b2d3f", "a9c1e3f5b7d9"),
        ("4e6a8c0b2d3f", "d5f7a9b1c3e4"),
        ("5b7d9f1a3c4e", "e7a9c1b3d5f7"),
        ("5f7b9d1c3e4a", "f8b0d2e4a6c8"),
        ("668d35b9d531", "0df3d8ee7496"),
        ("7bdeea6732b0", "a6de8f2759b3"),
        ("97b19c72c16e", "f7b9d1e3a5c8"),
        ("9d2e4f6a8b1c", "a8c0e2b4d6f8"),
        ("9e41c7a0d35b", "c5e7a9b1d3f5"),
        ("a1c3e5f7b9d2", "668d35b9d531"),
        ("a3b5c7d9e1f4", "f2a4b6c8d0e3"),
        ("a3c5e7f9b1d2", "f8a0b2c4d6e9"),
        ("a4c6e8b0d2f4", "c8e0a2b4d6f1"),
        ("a6de8f2759b3", None),
        ("a7c9e1b3d5f6", "e3a5c7b9d1f4"),
        ("a7c9e1b3d5f8", "f3a5c7e9b1d2"),
        ("a7c9e1f3b5d8", "f6b8d0e2a4c6"),
        ("a8c0e2b4d6f8", "f6b8d0a2c4e6"),
        ("a8c0e2f4b6d9", "a7c9e1b3d5f6"),
        ("a9c1e3f5b7d9", "e7a9c1d3f5b7"),
        ("b2c4d6e8f0a1", "c3e5a7b9d1f2"),
        ("b2d4f6a8c0e1", "a1c3e5f7b9d2"),
        ("b3c5d7e9f1a2", "3c5e7a9b1d2f"),
        ("b3d5f7a9c1e4", "c5e7a9b1d3f6"),
        ("b4c6d8e0f2a5", "a3b5c7d9e1f4"),
        ("b4d6f8a0c2e4", "a3c5e7f9b1d2"),
        ("b4d6f8a0c2e7", "e2a4c6e8f0b3"),
        ("b7d1f3a5c9e2", "e4a6c8d0b2f1"),
        ("b8d0f2a4c6e1", "c9e1a3b5d7f8"),
        ("b8d0f2a4c6e7", "d4f6a8c0e2b1"),
        ("b8d0f2a4c6e9", "a7c9e1f3b5d8"),
        ("b9d1f3a5c7e8", "f7b9d1e3a5c6"),
        ("c1e3a5b7d9f2", "d7f0a2c4e6b8"),
        ("c3e5a7b9d1f2", "f1b3d5a7c9e2"),
        ("c3e5a7f9b1d4", "b2d4f6a8c0e1"),
        ("c4e6a8b0d2f4", "b2c4d6e8f0a1"),
        ("c5d7e9f1a3b6", "b4c6d8e0f2a5"),
        ("c5e7a9b1d3f5", "b4d6f8a0c2e4"),
        ("c5e7a9b1d3f6", "a7c9e1b3d5f8"),
        ("c6e8a0b2d4f1", "c6e8a0b2d4f6"),
        ("c6e8a0b2d4f6", "b4d6f8a0c2e7"),
        ("c783c8336a18", "b8d0f2a4c6e1"),
        ("c7e9a1b3d5f8", "b7d1f3a5c9e2"),
        ("c8e0a2b4d6f1", "c4e6a8b0d2f4"),
        ("c9e1a3b5d7f0", "b8d0f2a4c6e9"),
        ("c9e1a3b5d7f8", "b8d0f2a4c6e7"),
        ("d2f4a6c8e0b3", "c6e8a0b2d4f1"),
        ("d4f6a8c0e2b1", "f4b6d8a0c2e5"),
        ("d4f6a8c0e2b3", "b3d5f7a9c1e4"),
        ("d4f6a8c0e2b4", "a4c6e8b0d2f4"),
        ("d4f6b8a0c2e3", "c3e5a7f9b1d4"),
        ("d5b7c9e1f3a4", "f3b5d7e9a1c2"),
        ("d5f7a9b1c3e4", "9e41c7a0d35b"),
        ("d6e8f0a2b4c7", "c5d7e9f1a3b6"),
        ("d6f8b0c2e4a6", "4e6a8c0b2d3f"),
        ("d7f0a2c4e6b8", "f2a4c6e8b0d1"),
        ("d9f1b3c5e7a2", "c7e9a1b3d5f8"),
        ("e1f3a5b7c9d2", "c9e1a3b5d7f0"),
        ("e2a4c6e8f0b3", "c783c8336a18"),
        ("e3a5c7b9d1f4", "d9f1b3c5e7a2"),
        ("e4a6c8d0b2f1", "b3c5d7e9f1a2"),
        ("e4a6c8d0f2b5", "d5b7c9e1f3a4"),
        ("e4a6c8f0b2d5", "9d2e4f6a8b1c"),
        ("e5a7c9b1d3f5", "d4f6b8a0c2e3"),
        ("e6a8b0c2d4f5", "4a6c8e0b2d3f"),
        ("e6a8c0f2b4d7", "e4a6c8f0b2d5"),
        ("e6b8d0f2a4c5", "d4f6a8c0e2b3"),
        ("e7a9c1b3d5f7", "b9d1f3a5c7e8"),
        ("e7a9c1d3f5b7", "2c4e6a8b0d1f"),
        ("e7f9a1b3c5d8", "d6e8f0a2b4c7"),
        ("f1b3d5a7c9e2", "5b7d9f1a3c4e"),
        ("f2a4b6c8d0e3", "e1f3a5b7c9d2"),
        ("f2a4c6e8b0d1", "e6b8d0f2a4c5"),
        ("f3a5c7e9b1d2", "d4f6a8c0e2b4"),
        ("f3b5d7e9a1c2", "d2f4a6c8e0b3"),
        ("f4b6d8a0c2e5", "a8c0e2f4b6d9"),
        ("f6b8d0a2c4e6", "c1e3a5b7d9f2"),
        ("f6b8d0e2a4c6", "e5a7c9b1d3f5"),
        ("f7b9d1e3a5c6", "e6a8b0c2d4f5"),
        ("f7b9d1e3a5c8", "e6a8c0f2b4d7"),
        ("f8a0b2c4d6e9", "e7f9a1b3c5d8"),
        ("f8b0d2e4a6c8", "d6f8b0c2e4a6"),
    }
)


def _load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_english_for_schemas", ROOT / "tools" / "check_english.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _parsed_graph() -> dict[str, str | tuple[str, ...] | None]:
    """Every migration's `revision` -> `down_revision`, read from the syntax tree.

    A revision that joins two branches names a tuple of parents (`20261114_1350_one_head_again.py`)."""
    graph: dict[str, str | tuple[str, ...] | None] = {}
    for path in sorted(VERSIONS.glob("*.py")):
        values: dict[str, str | tuple[str, ...] | None] = {}
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                name, value = node.target.id, node.value
            elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                name, value = node.targets[0].id, node.value
            else:
                continue
            if name in ("revision", "down_revision") and value is not None:
                values[name] = ast.literal_eval(value)
        revision = values["revision"]
        graph[revision if isinstance(revision, str) else ""] = values["down_revision"]
    return graph


def test_schemas_and_migrations_have_no_french() -> None:
    checker = _load_checker()
    findings = checker.scan_tree((API + "schemas/", API + "migrations/"))
    assert findings == [], "\n".join(
        f"{f.path}:{f.line}: {f.reason}: {f.text.strip()[:100]}" for f in findings
    )


def test_migration_graph_unchanged() -> None:
    graph = _parsed_graph()
    parsed = {(revision, parent) for revision, parent in graph.items()}
    missing = sorted(RECORDED - parsed, key=str)
    assert not missing, f"a recorded (revision, down_revision) pair changed or disappeared: {missing}"
    parents: set[str] = set()
    for parent in graph.values():
        parents.update((parent,) if isinstance(parent, str) else parent or ())
    heads = set(graph) - parents
    assert len(heads) == 1, f"the migration chain must have exactly one head, found {sorted(heads)}"


@pytest.mark.parametrize(
    ("former", "current"),
    [
        ("identite", "identity"),
        ("livraison", "delivery"),
        ("mesures", "measures"),
        ("plateforme", "platform"),
        ("projets", "projects"),
    ],
)
def test_former_schema_module_paths_are_gone(former: str, current: str) -> None:
    assert importlib.util.find_spec(f"choregos_api.schemas.{former}") is None
    assert importlib.import_module(f"choregos_api.schemas.{current}") is not None
