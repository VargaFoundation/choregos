"""A workflow chooses its next step from what the agent answered (S48-01, ADR 0106).

Before, `select_transition` read only the id of an incoming event (`ci_failed`, `changes_requested`,
`merged`) and otherwise took the first transition declared: an agent that answered "done: false" or
"outcome: reply" could not send the work item anywhere but the one path, and a workflow had to ask a
person to choose for it.
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_core import ValidationError, WorkflowEngine, parse_workflow
from choregos_core.dsl.conditions import when_holds

# "Develop XXX, then answer: is it finished? are there findings?" — the agent loops until it says it
# is done; findings send it to a fixer; a low confidence asks a person.
DEVELOP = """
apiVersion: choregos/v1
kind: Workflow
metadata: { name: develop, version: 1 }
initial: todo
actors:
  developer: { type: agent, role: implement }
  fixer: { type: agent, role: implement }
  platform: { type: system }
  lead: { type: human, group: maintainers }
states:
  todo: { display: To do }
  checkpoint: { display: Checkpoint }
  fixing: { display: Fixing }
  unsure: { display: Unsure, kind: wait }
  finished: { display: Finished, terminal: true }
transitions:
  - id: t-develop
    from: todo
    to: checkpoint
    by: developer
    outputs: [done, findings, confidence]
  - { id: t-unsure, from: checkpoint, to: unsure, by: platform, when: { confidence: { lt: 0.6 } } }
  - { id: t-again, from: checkpoint, to: todo, by: platform, when: { done: false } }
  - { id: t-fix, from: checkpoint, to: fixing, by: platform, when: { findings: { gt: 0 } } }
  - { id: t-finish, from: checkpoint, to: finished, by: platform }
  - { id: t-fixed, from: fixing, to: finished, by: fixer }
  - { id: t-decide, from: unsure, to: finished, by: lead }
"""


@pytest.fixture
def engine() -> WorkflowEngine:
    workflow, report = parse_workflow(DEVELOP)
    assert report.valid, report.errors
    return WorkflowEngine(workflow)


@pytest.mark.parametrize(
    ("outputs", "expected"),
    [
        ({"done": False, "findings": 0, "confidence": 0.9}, "t-again"),
        ({"done": "false", "findings": "0", "confidence": "0.9"}, "t-again"),  # JSON written loosely
        ({"done": True, "findings": 2, "confidence": 0.9}, "t-fix"),
        ({"done": True, "findings": 0, "confidence": 0.9}, "t-finish"),
        ({"done": True, "findings": 0, "confidence": 0.4}, "t-unsure"),  # first match wins
        ({"done": False, "findings": 0, "confidence": 0.4}, "t-unsure"),
        ({"done": "yes", "findings": 0}, "t-finish"),  # no confidence: its condition does not match
        (None, "t-finish"),  # no stage answered: the default
        ({}, "t-finish"),
    ],
)
def test_the_first_transition_whose_condition_holds_is_taken(
    engine: WorkflowEngine, outputs: dict[str, Any] | None, expected: str
) -> None:
    chosen = engine.select_transition("checkpoint", outputs=outputs)
    assert chosen is not None and chosen.id == expected


def test_an_incoming_event_still_wins(engine: WorkflowEngine) -> None:
    chosen = engine.select_transition("checkpoint", outcome="t-finish", outputs={"done": False})
    assert chosen is not None and chosen.id == "t-finish"


def test_a_workflow_without_when_chooses_as_before(engine: WorkflowEngine) -> None:
    chosen = engine.select_transition("todo", outputs={"anything": 1})
    assert chosen is not None and chosen.id == "t-develop"


@pytest.mark.parametrize(
    ("when", "outputs", "holds"),
    [
        ({"outcome": "pr"}, {"outcome": "PR "}, True),
        ({"outcome": ["pr", "reply"]}, {"outcome": "reply"}, True),
        ({"outcome": ["pr", "reply"]}, {"outcome": "skip"}, False),
        ({"outcome": {"not_in": ["skip"]}}, {"outcome": "pr"}, True),
        ({"outcome": {"not_in": ["skip"]}}, {"outcome": "skip"}, False),
        ({"confidence": {"gte": 0.8, "lte": 1}}, {"confidence": 0.8}, True),
        ({"confidence": {"gte": 0.8}}, {"confidence": "0.79"}, False),
        ({"confidence": {"gte": 0.8}}, {"confidence": "high"}, False),  # not a number: no match
        ({"confidence": {"gte": 0.8}}, {"confidence": True}, False),  # a boolean is not a number
        ({"findings": 0}, {"findings": "0.0"}, True),
        ({"done": True}, {"done": "TRUE"}, True),
        ({"done": True}, {"done": 1}, False),  # 1 is not `true`
        ({"done": True}, {"done": None}, False),
        ({"done": True}, {"other": True}, False),  # missing: never matches
        ({"outcome": "pr", "confidence": {"gte": 0.8}}, {"outcome": "pr", "confidence": 0.9}, True),
        ({"outcome": "pr", "confidence": {"gte": 0.8}}, {"outcome": "pr", "confidence": 0.5}, False),
    ],
)
def test_conditions(when: dict[str, Any], outputs: dict[str, Any], holds: bool) -> None:
    from choregos_contracts.workflow import Transition

    parsed = Transition.model_validate({"from": "a", "to": "b", "by": "x", "when": when}).when
    assert when_holds(parsed, outputs) is holds


def _codes(text: str) -> list[str]:
    try:
        _workflow, report = parse_workflow(text, strict=False)
    except ValidationError as exc:
        return [i.code for i in exc.issues]
    return [i.code for i in report.errors]


def test_a_state_with_conditional_exits_needs_a_default() -> None:
    without_default = DEVELOP.replace(
        "  - { id: t-finish, from: checkpoint, to: finished, by: platform }\n",
        "  - { id: t-finish, from: checkpoint, to: finished, by: platform, when: { done: true } }\n",
    )
    assert "when.no_default" in _codes(without_default)


def test_a_condition_names_a_declared_output() -> None:
    typo = DEVELOP.replace("when: { done: false }", "when: { dnoe: false }")
    assert "when.unknown_output" in _codes(typo)


def test_a_malformed_condition_is_refused() -> None:
    assert _codes(DEVELOP.replace("when: { done: false }", "when: { done: { bigger: 1 } }"))
    assert _codes(DEVELOP.replace("when: { done: false }", "when: { Done: false }"))
