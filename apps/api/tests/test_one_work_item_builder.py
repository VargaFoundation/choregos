"""One work item builder: `services.routing.new_work_item` (ADR 0031).

Five paths created a work item, and two set the `inbox` state by hand: on a workflow that starts
elsewhere, the work item died at birth (#173). The single builder sets the initial state of the
chosen workflow and the pin of its version together; this guard refuses a sixth path going around it.
"""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[3]
ALLOWED = {ROOT / "apps/api/src/choregos_api/services/routing.py"}


def test_no_work_item_is_built_outside_new_work_item() -> None:
    offenders = []
    for folder in [ROOT / "apps", ROOT / "packages"]:
        for file in folder.glob("*/src/**/*.py"):
            if file in ALLOWED:
                continue
            for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "WorkItem":
                    offenders.append(f"{file.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, f"WorkItem( outside new_work_item: {offenders}"
