"""One constructor of human requests: `services.human_requests.new_human_request` (S47-16).

Three paths built a `HumanRequest`, and two of them forgot the due date, so agent questions and
scope changes showed no SLA in the inbox. This guard refuses a fourth construction site.
"""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[3]
ALLOWED = {ROOT / "apps/api/src/choregos_api/services/human_requests.py"}


def constructions(root: pathlib.Path) -> list[tuple[pathlib.Path, int]]:
    """Every call `HumanRequest(...)` or `models.HumanRequest(...)` under `root`."""
    hits = []
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (isinstance(func, ast.Name) and func.id == "HumanRequest") or (
                isinstance(func, ast.Attribute) and func.attr == "HumanRequest"
            ):
                hits.append((path, node.lineno))
    return hits


def test_human_requests_are_built_in_one_place() -> None:
    offenders = []
    for base in ("apps", "packages"):
        for src in (ROOT / base).glob("*/src"):
            offenders += [
                f"{path.relative_to(ROOT)}:{line}" for path, line in constructions(src) if path not in ALLOWED
            ]
    assert not offenders, f"HumanRequest( outside new_human_request: {offenders}"


def test_the_detector_sees_a_construction(tmp_path: pathlib.Path) -> None:
    (tmp_path / "sample.py").write_text("HumanRequest(x=1)\nmodels.HumanRequest(x=1)\n", encoding="utf-8")
    assert len(constructions(tmp_path)) == 2
