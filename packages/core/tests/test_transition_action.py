"""A system transition proposes a governed action, on a date (S20-05): what the core says about it:
the date drawn from a field, what the validator refuses, what follows the action, the process sentence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from choregos_core.dsl import parse_workflow, to_process
from choregos_core.dsl.dates import UnreadableDate, due_date, field_of
from choregos_core.dsl.engine import WorkflowEngine
from choregos_core.gates import GateContext, evaluate

ARRIVAL = """
apiVersion: choregos/v1
kind: Workflow
metadata:
  name: onboarding
  version: 1
  inputs:
    type: object
    properties:
      upn: {type: string}
      arrival_date: {type: string, format: date}
      position: {type: string}
initial: prepare
actors:
  platform: {type: system}
  hr: {type: human, group: hr}
states:
  prepare: {display: Prepared, kind: wait}
  accounts: {display: Accounts created, kind: wait}
  to_review: {display: To review, kind: wait}
  done: {display: Done, terminal: true}
transitions:
  - id: t-accounts
    from: prepare
    to: accounts
    by: platform
    action:
      kind: arrival.accounts
      title: "The accounts of {{ fields.upn }}"
      params: {upn: "{{ fields.upn }}"}
      effects:
        - effect: connector.call
          with: {connector: entra, operation: create_user, arguments: {upn: "{{ params.upn }}"}}
      not_before: fields.arrival_date - 10d
    on_fail: {to: prepare, max_attempts: 2, escalate_to: to_review}
    on_reject: to_review
  - {from: accounts, to: done, by: hr}
"""


def _arrival(**replacements: str) -> tuple[Any, Any]:
    text = ARRIVAL
    for old, new in replacements.items():
        text = text.replace(old, new)
    return parse_workflow(text, strict=False)


def _codes(report: Any) -> list[str]:
    return [e.code for e in report.errors]


@pytest.mark.parametrize(
    ("expression", "fields", "expected"),
    [
        ("fields.arrival_date - 10d", {"arrival_date": "2026-11-12"}, datetime(2026, 11, 2, tzinfo=UTC)),
        ("fields.arrival_date", {"arrival_date": "2026-11-12"}, datetime(2026, 11, 12, tzinfo=UTC)),
        (
            "fields.departure + 2h",
            {"departure": "2026-11-12T17:30:00+01:00"},
            datetime(2026, 11, 12, 18, 30, tzinfo=UTC),
        ),
        (
            "fields.departure - 90m",
            {"departure": "2026-11-12T10:00:00Z"},
            datetime(2026, 11, 12, 8, 30, tzinfo=UTC),
        ),
        ("fields.departure", {"departure": "2026-11-12T10:00:00"}, datetime(2026, 11, 12, 10, tzinfo=UTC)),
    ],
)
def test_a_date_is_drawn_from_a_field_shifted_and_in_utc(
    expression: str, fields: dict[str, Any], expected: datetime
) -> None:
    assert due_date(expression, fields) == expected


def test_without_the_field_no_date_and_an_unreadable_date_is_said() -> None:
    assert due_date("fields.arrival_date - 10d", {}) is None, (
        "we wait for the field, we do not leave without a date"
    )
    assert due_date("fields.arrival_date - 10d", {"arrival_date": ""}) is None
    with pytest.raises(UnreadableDate, match="arrival_date"):
        due_date("fields.arrival_date", {"arrival_date": "next monday"})
    with pytest.raises(ValueError, match="unreadable"):
        field_of("tomorrow - 10d")
    assert field_of("fields.arrival_date - 10d") == "arrival_date"


def test_a_well_formed_workflow_with_an_action_is_valid() -> None:
    _, report = _arrival()
    assert report.valid, report.as_dict()


WITHOUT_DATE = "arrival_date: {type: string}"
RETRY = "    on_fail: {to: prepare, max_attempts: 2, escalate_to: to_review}\n"
VALIDATION = "  - {from: accounts, to: done, by: hr}"
VALIDATION_GUARANTEE = "  - {from: accounts, to: done, by: hr, gates: [action_succeeded]}"


@pytest.mark.parametrize(
    ("old", "new", "code"),
    [
        ("    by: platform\n    action:", "    by: hr\n    action:", "action.not_system"),
        (
            "not_before: fields.arrival_date",
            "not_before: fields.departure_date",
            "action.date_field_unknown",
        ),
        ("arrival_date: {type: string, format: date}", WITHOUT_DATE, "action.date_field_not_date"),
        (RETRY, "", "action.on_fail_missing"),
        (VALIDATION, VALIDATION_GUARANTEE, "gate.nothing_to_check"),
    ],
)
def test_the_validator_refuses_what_an_action_cannot_hold(old: str, new: str, code: str) -> None:
    _, report = _arrival(**{old: new})
    assert code in _codes(report), report.as_dict()


def test_a_rejected_action_follows_on_reject_a_failed_one_on_fail_a_succeeded_one_moves_on() -> None:
    workflow, _ = _arrival()
    engine = WorkflowEngine(workflow)
    transition = workflow.transitions[0]
    assert engine.after_action(transition, "succeeded", [], 1).next_state == "accounts"
    assert engine.after_action(transition, "rejected", [], 1).next_state == "to_review"
    retry = engine.after_action(transition, "failed", [], 1)
    assert (retry.next_state, retry.retried) == ("prepare", True)
    assert engine.after_action(transition, "failed", [], 2).next_state == "to_review", (
        "two attempts, then the escalation"
    )
    without_rejection = transition.model_copy(update={"on_reject": None})
    assert engine.after_action(without_rejection, "rejected", [], 2).next_state == "to_review", (
        "a rejection is a failure"
    )


@pytest.mark.parametrize(
    ("status", "passes", "waits"),
    [(None, False, True), ("pending_approval", False, True), ("running", False, True),
     ("succeeded", True, False), ("failed", False, False), ("rejected", False, False)],
)  # fmt: skip
def test_action_succeeded_waits_then_rules(status: str | None, passes: bool, waits: bool) -> None:
    verdict = evaluate("action_succeeded", GateContext(action_status=status))
    assert (verdict.passed, verdict.pending) == (passes, waits)


def test_an_action_in_doubt_does_not_settle_its_transition_which_waits() -> None:
    """ADR 0149 §5: an action in doubt is never replayed. Read as a failure it would follow `on_fail`,
    whose retry proposes the action again under a new attempt: a second delivery of a write nobody
    knows was made. So the transition's guarantee waits, and the work item stays where it is."""
    from choregos_core.gates import ACTION_SETTLED

    assert "in_doubt" not in ACTION_SETTLED
    verdict = evaluate("action_succeeded", GateContext(action_status="in_doubt"))
    assert (verdict.passed, verdict.pending, verdict.blocking) == (False, True, False)
    assert "in_doubt" in verdict.detail
    workflow, _ = _arrival()
    transition = workflow.transitions[0]
    waits = WorkflowEngine(workflow).after_action(transition, "in_doubt", [], 1)
    assert (waits.next_state, waits.retried, waits.escalated) == ("prepare", False, False)


def test_the_process_view_says_the_action_its_date_and_its_implicit_guarantee() -> None:
    workflow, _ = _arrival()
    step = to_process(workflow)[0]
    assert [g["name"] for g in step["gates"]] == ["action_succeeded"]
    assert "`arrival.accounts` not before `fields.arrival_date - 10d`" in step["sentence"]
    assert "approved by a person when one of its operations requires it" in step["action"]
    assert "its governed action succeeded" in step["sentence"]
