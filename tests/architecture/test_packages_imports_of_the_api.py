"""Only the ontology service still imports the API, and never more than today (ADR 0099)."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = ROOT / "packages"
ALLOWED = Path("ontology") / "src" / "choregos_ontology" / "service"

# Measured at 87ea1831d. Lower it in the PR that removes imports; raise it only for a mechanical
# rename that splits an existing statement, saying so in the PR body, or by amending ADR 0099.
CEILING = 22


def _is_api(name: str) -> bool:
    return name == "choregos_api" or name.startswith("choregos_api.")


def api_imports_by_file(packages: Path) -> dict[Path, int]:
    """The number of `choregos_api` import statements of each file under packages/*/src."""
    found: dict[Path, int] = {}
    for path in packages.glob("*/src/**/*.py"):
        count = 0
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module and _is_api(node.module):
                    count += 1
            elif isinstance(node, ast.Import):
                count += sum(1 for alias in node.names if _is_api(alias.name))
        if count:
            found[path.relative_to(packages)] = count
    return found


def violations(found: dict[Path, int]) -> list[str]:
    return sorted(str(path) for path in found if not path.is_relative_to(ALLOWED))


def test_only_the_ontology_service_imports_the_api() -> None:
    found = api_imports_by_file(PACKAGES)
    assert not violations(found), (
        f"packages importing choregos_api outside choregos_ontology/service: {violations(found)} "
        "(ADR 0099: move shared code to packages/core)"
    )
    total = sum(found.values())
    assert total <= CEILING, (
        f"packages/*/src imports choregos_api {total} times, the ceiling is {CEILING}: "
        "a new import is refused (ADR 0099: move shared code to packages/core)"
    )


def test_the_detector_counts_and_places_imports(tmp_path: Path) -> None:
    inside = tmp_path / "ontology/src/choregos_ontology/service/a.py"
    outside = tmp_path / "core/src/choregos_core/b.py"
    for path, source in (
        (inside, "from choregos_api.db import x\nimport choregos_api.config\n"),
        (outside, "from choregos_api.db import y\n"),
    ):
        path.parent.mkdir(parents=True)
        path.write_text(source, encoding="utf-8")
    found = api_imports_by_file(tmp_path)
    assert sum(found.values()) == 3
    assert violations(found) == ["core/src/choregos_core/b.py"]
