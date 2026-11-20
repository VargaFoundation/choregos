"""A transition declares the outcome it repairs (ADR 0181 C1): `repairs: ci_failed`.

The platform used to guess a repair from the agent's role: `fix_ci` for `ci_failed`, `address_review`
for `changes_requested`, roles of one trade hard-coded in the core's selection. A workflow now says
which transition repairs which outcome, for any role; a workflow stored before `repairs:` existed keeps
the guess, since stored versions are never rewritten.
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from pydantic import ValidationError
from tranibor_contracts import Workflow
from tranibor_core import WorkflowEngine
from tranibor_core.dsl import template_yaml

DESK = """apiVersion: choregos/v1
kind: Workflow
metadata: { name: desk, version: 1 }
actors:
  writer: { type: agent, role: implement }
  mender: { type: agent, role: mend }
  answerer: { type: agent, role: answer }
states:
  checking: { display: Checking }
  done: { display: Done, terminal: true }
transitions:
  - { id: t-redo, from: checking, to: done, by: writer }
  - { id: t-mend, from: checking, to: done, by: mender, repairs: ci_failed }
  - { id: t-answer, from: checking, to: done, by: answerer, repairs: changes_requested }
"""

GUESSED = """apiVersion: choregos/v1
kind: Workflow
metadata: { name: guessed, version: 1 }
actors:
  ci_fixer: { type: agent, role: fix_ci }
  writer: { type: agent, role: implement }
states:
  checking: { display: Checking }
  done: { display: Done, terminal: true }
transitions:
  - { id: t-fix, from: checking, to: done, by: ci_fixer }
  - { id: t-redo, from: checking, to: done, by: writer }
"""


def _engine(text: str) -> WorkflowEngine:
    return WorkflowEngine(Workflow.model_validate(yaml.safe_load(text)))


def _picked(engine: WorkflowEngine, outcome: str | None, **kwargs: Any) -> str | None:
    found = engine.select_transition("checking", outcome, **kwargs)
    return found.id if found else None


def test_the_transition_that_repairs_an_outcome_is_taken_on_it_and_never_otherwise() -> None:
    engine = _engine(DESK)
    assert engine.declares_repairs
    assert _picked(engine, "ci_failed") == "t-mend"
    assert _picked(engine, "changes_requested") == "t-answer"
    assert _picked(engine, None) == "t-redo"
    assert _picked(engine, "merged") == "t-redo", "an outcome nothing repairs takes the normal path"
    assert _picked(engine, "t-answer") == "t-answer", "a transition's id still wins"


def test_a_history_before_the_patch_does_not_read_repairs() -> None:
    """`repairs=False` is the interpreter's answer without the `repairs-declared` marker."""
    engine = _engine(DESK)
    assert _picked(engine, "ci_failed", repairs=False) == "t-redo"
    assert _picked(engine, None, repairs=False) == "t-redo"


def test_a_workflow_that_declares_none_keeps_the_guess_from_the_role() -> None:
    engine = _engine(GUESSED)
    assert not engine.declares_repairs
    assert _picked(engine, "ci_failed") == "t-fix"
    assert _picked(engine, None) == "t-redo", "the guessed repair is left out of the normal path"


def test_a_declared_repair_turns_the_guess_off() -> None:
    document = yaml.safe_load(GUESSED)
    document["transitions"][1]["repairs"] = "changes_requested"
    engine = WorkflowEngine(Workflow.model_validate(document))
    assert _picked(engine, "ci_failed") == "t-fix", "nothing repairs it: the normal path, in order"
    assert _picked(engine, "changes_requested") == "t-redo"
    assert _picked(engine, None) == "t-fix"


def test_repairs_names_an_outcome() -> None:
    document = yaml.safe_load(DESK)
    document["transitions"][1]["repairs"] = "CI failed"
    with pytest.raises(ValidationError):
        Workflow.model_validate(document)


def test_an_absent_repairs_is_left_out_of_the_stored_spec() -> None:
    document = yaml.safe_load(GUESSED)
    stored = Workflow.model_validate(document).model_dump(mode="json", by_alias=True, exclude_none=True)
    assert all("repairs" not in t for t in stored["transitions"])


@pytest.mark.parametrize(
    "name",
    [
        "advanced",
        "dependency-upgrade",
        "dev-feature",
        "dev-fix",
        "documentation",
        "full-auto",
        "hotfix",
        "refactoring",
        "vulnerability-fix",
    ],
)
def test_the_shipped_templates_declare_what_their_repairs_repair(name: str) -> None:
    workflow = Workflow.model_validate(yaml.safe_load(template_yaml(name)))
    by_role = {
        str(actor.role): actor_name
        for actor_name, actor in workflow.actors.items()
        if getattr(actor, "role", None) in ("fix_ci", "address_review")
    }
    repairs = {t.by: t.repairs for t in workflow.transitions if t.by in by_role.values()}
    expected = {"fix_ci": "ci_failed", "address_review": "changes_requested"}
    assert repairs == {by_role[role]: outcome for role, outcome in expected.items() if role in by_role}
