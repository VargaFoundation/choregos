"""The orchestrator depends on the API less and less: today's imports are frozen (ADR 0099)."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATOR_SRC = ROOT / "apps" / "orchestrator" / "src"

# Measured at 87ea1831d. Lower it in the PR that removes imports; raise it only for a mechanical
# rename that splits an existing statement, saying so in the PR body, or by amending ADR 0099.
CEILING = 153

HTTP_LAYER = (
    "choregos_api.routers",
    "choregos_api.deps",
    "choregos_api.main",
    "choregos_api.mcp",
    "choregos_api.a2a",
    "fastapi",
)


def _is_api(name: str) -> bool:
    return name == "choregos_api" or name.startswith("choregos_api.")


def imported_modules(root: Path) -> list[str]:
    """Every absolute module named by an import statement under `root`, one entry per alias."""
    modules: list[str] = []
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.append(node.module)
            elif isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
    return modules


def count_api_imports(root: Path) -> int:
    """Count the `choregos_api` import statements under `root` (one per `from`, one per alias)."""
    count = 0
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module and _is_api(node.module):
                    count += 1
            elif isinstance(node, ast.Import):
                count += sum(1 for alias in node.names if _is_api(alias.name))
    return count


def test_the_orchestrator_imports_no_more_of_the_api() -> None:
    count = count_api_imports(ORCHESTRATOR_SRC)
    assert count <= CEILING, (
        f"apps/orchestrator imports choregos_api {count} times, the ceiling is {CEILING}: "
        "a new import is refused (ADR 0099: move shared code to packages/core)"
    )


def test_the_orchestrator_never_imports_the_http_layer() -> None:
    offenders = sorted(
        {
            module
            for module in imported_modules(ORCHESTRATOR_SRC)
            if any(module == banned or module.startswith(banned + ".") for banned in HTTP_LAYER)
        }
    )
    assert not offenders, f"the orchestrator imports the HTTP layer: {offenders} (ADR 0099)"


def test_the_counter_sees_both_import_forms(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text(
        "from choregos_api.db import x\nimport choregos_api.config\nimport os\n", encoding="utf-8"
    )
    assert count_api_imports(tmp_path) == 2
