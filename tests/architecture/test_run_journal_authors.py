"""The two authors of a run's journal stay apart (S65-08, ADR 0173).

- The platform writes a line of a run's journal with a type taken from `tranibor_core.run_journal`,
  so that a new one cannot appear outside the namespaces the run's door refuses.
- The run's door is the one place that writes a type it was sent.
- Nothing the runner or its tool bridge records is in a reserved namespace: reserving it would
  silently drop their lines.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tranibor_core import run_journal
from tranibor_core.run_journal import PLATFORM_EVENTS, platform_written

ROOT = Path(__file__).resolve().parents[2]
SOURCES = [*sorted(ROOT.glob("apps/*/src")), *sorted(ROOT.glob("packages/*/src"))]

#: The run's own door: it writes the type it was sent, after refusing the platform's.
RUN_DOOR = "apps/api/src/tranibor_api/routers/internal.py"

#: The names of `tranibor_core.run_journal` that are a platform type.
PLATFORM_NAMES = {name for name in run_journal.__all__ if getattr(run_journal, name) in PLATFORM_EVENTS}

#: Lines the platform adds under a type the run writes too, by file and by the name the type is
#: taken from. Such a type cannot be reserved: the run's door would refuse the run's own lines.
#: `context.breakdown`: the runner records the context it measured; the API adds the gateway's count
#: (`source: gateway`, above `GATEWAY_SEQ_BASE`). A number, not a verdict; the run's door refuses the
#: gateway's shape of it (S65-08b, `gateway_written`).
SHARED_TYPES = {"apps/api/src/tranibor_api/services/context_breakdown.py": "EVENT"}

#: Who records a run's own lines: the runner and its tool bridge.
RUN_SIDE = ["packages/runner/src", "packages/tools-mcp/src"]


def _name(node: ast.expr | None) -> str | None:
    """The last name of `x` or `module.x`; `None` for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    return node.attr if isinstance(node, ast.Attribute) else None


def _calls(tree: ast.AST, name: str) -> list[ast.Call]:
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _name(node.func) == name:
            found.append(node)
    return found


def _journal_lines() -> dict[str, list[ast.Call]]:
    """Every `RunEvent(...)` built in the sources, by file."""
    lines: dict[str, list[ast.Call]] = {}
    for source in SOURCES:
        for path in sorted(source.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "RunEvent(" not in text:
                continue
            calls = _calls(ast.parse(text), "RunEvent")
            if calls:
                lines[path.relative_to(ROOT).as_posix()] = calls
    return lines


def _type_of(call: ast.Call) -> ast.expr | None:
    return next((keyword.value for keyword in call.keywords if keyword.arg == "type"), None)


def test_the_platform_takes_its_journal_types_from_one_module() -> None:
    lines = _journal_lines()
    assert RUN_DOOR in lines, "the run's door no longer writes the journal: this guard reads the wrong file"
    wrong = []
    for path, calls in lines.items():
        if path == RUN_DOOR:
            continue
        for call in calls:
            name = _name(_type_of(call))
            if name not in PLATFORM_NAMES and SHARED_TYPES.get(path) != name:
                wrong.append(f"{path}:{call.lineno}")
    assert not wrong, (
        "a line the platform writes in a run's journal takes its type from tranibor_core.run_journal "
        f"(one of {sorted(PLATFORM_NAMES)}), so that the run's door refuses it from a run: {wrong}"
    )
    assert len(lines) > 1, "no platform writer was found: the guard would pass on nothing"
    gone = [path for path in SHARED_TYPES if path not in lines]
    assert not gone, f"a shared type's writer no longer writes the journal, drop it from SHARED_TYPES: {gone}"


def test_only_the_runs_door_writes_a_type_it_was_sent() -> None:
    door = _journal_lines()[RUN_DOOR]
    assert len(door) == 1, "one place writes what a run sends; a second one needs the same refusal"
    source = (ROOT / RUN_DOOR).read_text(encoding="utf-8")
    assert _calls(ast.parse(source), "platform_written"), "the run's door no longer asks who may write a type"


def test_nothing_the_run_side_records_is_reserved() -> None:
    recorded: dict[str, str] = {}
    for folder in RUN_SIDE:
        for path in sorted((ROOT / folder).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            where = path.relative_to(ROOT).as_posix()
            for call in _calls(tree, "record"):
                first = call.args[0] if call.args else None
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    recorded[first.value] = f"{where}:{call.lineno}"
            # Types kept as module constants (`EVENT = "shim.refused"`): any upper-case string constant
            # that looks like an event type.
            for node in tree.body:
                if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
                    continue
                value = node.value.value
                if isinstance(value, str) and all(
                    isinstance(target, ast.Name) and target.id.isupper() for target in node.targets
                ):
                    recorded.setdefault(value, f"{where}:{node.lineno}")
    assert len(recorded) > 20, "the run side's types were not found: the guard would pass on nothing"
    reserved = {name: where for name, where in recorded.items() if platform_written(name)}
    assert not reserved, f"the run side records a type the run's door refuses: {reserved}"
