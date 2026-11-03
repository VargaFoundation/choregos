# SPDX-License-Identifier: Apache-2.0
"""S47-32: `workflows/interpreter.py` stays small, and every Temporal handler stays on the class.

Temporal discovers the handlers of a workflow on its class; they are never moved to a mixin. The
patch ids stay in `interpreter.py` too (`tests/replay/test_english_names.py` pins their format).
"""

from __future__ import annotations

from pathlib import Path

from choregos_orchestrator.workflows import interpreter
from choregos_orchestrator.workflows.interpreter import WorkflowInterpreter

INTERPRETER = Path(interpreter.__file__)
MAX_LINES = 700
#: The attributes `temporalio.workflow` sets on a decorated method (signal, query, update, run).
DEFINITION_ATTRIBUTES = (
    "__temporal_signal_definition",
    "__temporal_query_definition",
    "__temporal_update_definition",
    "__temporal_workflow_run",
)
#: Handlers the workflow declares; adding one without declaring it here is the point of the test.
EXPECTED_HANDLERS = {
    "human_decision",
    "inbound",
    "question_asked",
    "finding",
    "fields_changed",
    "child_settled",
    "action_settled",
    "control",
    "status",
    "run",
}


def _is_handler(member: object) -> bool:
    return any(hasattr(member, attribute) for attribute in DEFINITION_ATTRIBUTES)


def test_interpreter_module_stays_under_700_lines() -> None:
    lines = len(INTERPRETER.read_text(encoding="utf-8").splitlines())
    assert lines <= MAX_LINES, (
        f"workflows/interpreter.py has {lines} lines: move helpers to interpreter_parts/"
    )


def test_every_handler_is_defined_on_the_class_itself() -> None:
    declared = {name for name, member in vars(WorkflowInterpreter).items() if _is_handler(member)}
    inherited = {
        name
        for base in WorkflowInterpreter.__mro__[1:]
        for name, member in vars(base).items()
        if _is_handler(member)
    }
    assert not inherited, f"handlers defined on a base class, where nothing guarantees discovery: {inherited}"
    assert declared == EXPECTED_HANDLERS


def test_the_names_other_modules_import_are_still_there() -> None:
    for name in (
        "DEFAULT_RETRY",
        "POLL_RETRY",
        "NO_RETRY",
        "CONFIGURATION_REFUSALS",
        "STAGE_CONFIG_REFUSAL_ESCALATES",
        "InterpreterInput",
        "wait_signal",
    ):
        assert hasattr(interpreter, name), name
