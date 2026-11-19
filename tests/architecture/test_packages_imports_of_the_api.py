"""No package imports the API (ADR 0099).

The ontology service was the last one: it went with the package (ADR 0181, O4-1), and the ceiling of
22 import statements it held fell to zero. A package that needs code of the API moves that code to
`packages/core` instead.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = ROOT / "packages"

# Was 22 (measured at 87ea1831d, all in the ontology service), 0 since the ontology was removed. It
# is raised only by amending ADR 0099.
CEILING = 0


def _is_api(name: str) -> bool:
    return name == "tranibor_api" or name.startswith("tranibor_api.")


def api_imports_by_file(packages: Path) -> dict[Path, int]:
    """The number of `tranibor_api` import statements of each file under packages/*/src."""
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


def test_no_package_imports_the_api() -> None:
    found = api_imports_by_file(PACKAGES)
    total = sum(found.values())
    assert total <= CEILING, (
        f"packages/*/src imports tranibor_api {total} times, the ceiling is {CEILING}: "
        f"{sorted(str(path) for path in found)} (ADR 0099: move shared code to packages/core)"
    )


def test_the_detector_counts_and_places_imports(tmp_path: Path) -> None:
    for path, source in (
        (
            tmp_path / "plugin/src/tranibor_plugin/a.py",
            "from tranibor_api.db import x\nimport tranibor_api.config\n",
        ),
        (tmp_path / "core/src/tranibor_core/b.py", "from tranibor_api.db import y\nimport tranibor_core\n"),
    ):
        path.parent.mkdir(parents=True)
        path.write_text(source, encoding="utf-8")
    found = api_imports_by_file(tmp_path)
    assert found == {Path("plugin/src/tranibor_plugin/a.py"): 2, Path("core/src/tranibor_core/b.py"): 1}
