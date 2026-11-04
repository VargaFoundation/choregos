"""What the orchestrator writes for someone is in English (ADR 0039, S21-13).

The ticket's tracking comment, the notifications, the human requests, the reasons of a state, the
provisioning steps, the findings issues, the output of `make demo`: all of it is read in the
tracker, Slack or the console. The code, the comments, the docstrings and the logs stay in
French. The `STILL_FRENCH_LITERALS` list can only shrink: an entry that matches nothing any more
turns the suite red.
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
ORCHESTRATOR = ROOT / "apps/orchestrator/src/choregos_orchestrator"
LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical", "warn"}

#: (relative file, start of the literal), each with its reason.
STILL_FRENCH_LITERALS = {
    # A regular expression, not a text.
    ("activities/findings.py", "[a-zà-ÿ0-9_]+"),
    # The former names of the board fields (`Coût (€)`, `Taille`, `Risque`) have not been written by
    # the orchestrator since S22-21: they live outside it, in
    # `choregos_adapters/tracker/fields.py` (FORMER_NAMES), for the time of a rename. lang: fr-ok
}


def _detector() -> object:
    """The shipped-text detector of `tools/check_english.py`, loaded by its path (ADR 0098)."""
    spec = importlib.util.spec_from_file_location("check_english", ROOT / "tools" / "check_english.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass needs its module registered
    spec.loader.exec_module(module)
    return module.looks_like_shipped_french


def _french_literals() -> set[tuple[str, str, int]]:
    is_french = _detector()
    found: set[tuple[str, str, int]] = set()
    for path in sorted(ORCHESTRATOR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        to_skip: set[int] = set()
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and body:
                first = body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                    to_skip.add(id(first.value))
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in LOG_METHODS
            ):
                to_skip.update(id(inner) for inner in ast.walk(node))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)) or id(node) in to_skip:
                continue
            if len(node.value) > 3 and is_french(node.value):  # type: ignore[operator]
                found.add((path.relative_to(ORCHESTRATOR).as_posix(), node.value, node.lineno))
    return found


def _allowed(file: str, text: str) -> bool:
    return any(file == f and text.startswith(start) for f, start in STILL_FRENCH_LITERALS)


def test_the_orchestrator_writes_no_french_to_its_readers() -> None:
    leftovers = sorted(
        f"{file}:{line}: {text[:80]!r}" for file, text, line in _french_literals() if not _allowed(file, text)
    )
    assert not leftovers, "\n".join(leftovers)


def test_the_exception_list_only_shrinks() -> None:
    found = {(file, text) for file, text, _ in _french_literals()}
    stale = [
        entry
        for entry in sorted(STILL_FRENCH_LITERALS)
        if not any(f == entry[0] and t.startswith(entry[1]) for f, t in found)
    ]
    assert not stale, f"remove from STILL_FRENCH_LITERALS: {stale}"


def test_the_tracking_comment_and_a_human_request_read_in_english() -> None:
    from choregos_orchestrator.markdown import StageLine, StatusComment, render_human_request

    line = StageLine(
        1, "Refining", "agent refine", "opencode", "standard", 12_400, 1_800, 0, 0.42, 7_380, "spec written"
    )
    rendered = StatusComment(
        workflow="dev-simple", workflow_version=1, size="S", risk="low", state_display="Implemented",
        budget_eur=9.2, spent_eur=0.42, estimate_eur=0.5, lines=[line],
    ).render()  # fmt: skip
    assert "### Choregos — progress" in rendered
    assert "**Size** S · **Risk** low · **State** Implemented" in rendered
    assert "| # | Step | Actor |" in rendered and "€0.42" in rendered and "2 h 3 min" in rendered
    assert "estimated €0.50" in rendered
    request = render_human_request("approval", {}, "https://choregos.example/p/dev/items/1", "dev#1")
    assert "### Choregos — approval requested" in request
    assert "Approve or refuse in the console: https://choregos.example/p/dev/items/1" in request


#: The board fields from before S22-21: names a tracker still carries, that nothing writes any more.
FORMER_BOARD_NAMES = {"Coût (€)", "Taille", "Risque"}  # lang: fr-ok
COMPATIBILITY_TABLE = "packages/adapters/src/choregos_adapters/tracker/fields.py"


def test_the_former_board_names_live_only_in_the_compatibility_table() -> None:
    """S22-21 (#290): the orchestrator, the adapters and the templates name `Cost (€)`, `Size`,
    `Risk`. The French names stay only in `FORMER_NAMES`, to rename a former board and write to it
    until it is renamed: one version, then the table goes away."""
    where: dict[str, set[str]] = {}
    for root in (ORCHESTRATOR, ROOT / "packages/adapters/src/choregos_adapters"):
        for path in sorted(root.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Constant) and node.value in FORMER_BOARD_NAMES:
                    where.setdefault(path.relative_to(ROOT).as_posix(), set()).add(node.value)
    for manifest in sorted((ROOT / "templates").rglob("manifest.yaml")):
        text = manifest.read_text(encoding="utf-8")
        for name in FORMER_BOARD_NAMES:
            if name in text:
                where.setdefault(manifest.relative_to(ROOT).as_posix(), set()).add(name)
    assert where == {COMPATIBILITY_TABLE: FORMER_BOARD_NAMES}
