# SPDX-License-Identifier: Apache-2.0
"""What the decision ledger records of an agent, when a decision is right, and the validator's warning
on a threshold with no person below it (ADR 0121 §6, §7, S52-05)."""

from __future__ import annotations

import pytest
from choregos_core import parse_workflow
from choregos_core.decision_ledger import (
    MAX_ANSWER,
    Answered,
    declared_confidences,
    declares_a_confidence,
    is_observed,
    is_right,
    probability_of,
)

# ───────────────────────── an agent's declared confidence ─────────────────────────


def test_a_name_confidence_is_the_probability_of_that_name() -> None:
    assert declared_confidences({"route": "billing", "route_confidence": 0.83, "summary": "…"}) == [
        Answered("route", "billing", 0.83)
    ]


def test_a_bare_confidence_belongs_to_the_output_declared_just_before_it() -> None:
    outputs = {"confidence": 0.7, "done": True, "findings": 2}
    assert declared_confidences(outputs, ["findings", "done", "confidence"]) == [Answered("done", True, 0.7)]
    assert declared_confidences(outputs, ["done", "findings", "confidence"]) == [Answered("findings", 2, 0.7)]


def test_undeclared_a_bare_confidence_belongs_to_the_output_written_just_before_it() -> None:
    outputs = {"outcome": "pr", "confidence": "0.9"}
    assert declared_confidences(outputs, ["outcome"]) == [Answered("outcome", "pr", 0.9)]
    assert declared_confidences({"confidence": 0.9, "outcome": "pr"}) == [], "nothing before it"


def test_a_bare_confidence_does_not_take_an_answer_that_has_its_own() -> None:
    outputs = {"route": "tech", "route_confidence": 0.6, "confidence": 0.9}
    assert declared_confidences(outputs, ["route", "route_confidence", "confidence"]) == [
        Answered("route", "tech", 0.6)
    ]
    assert declared_confidences(outputs, ["route_confidence", "route", "confidence"]) == [
        Answered("route", "tech", 0.6)
    ]


@pytest.mark.parametrize(
    "outputs",
    [
        {"route": "billing", "route_confidence": 1.2},  # not a probability
        {"route": "billing", "route_confidence": -0.1},
        {"route": "billing", "route_confidence": True},  # a boolean is not a number
        {"route": "billing", "route_confidence": "sure"},
        {"route": "billing", "route_confidence": float("nan")},
        {"route_confidence": 0.8},  # no answer
        {"route": None, "route_confidence": 0.8},
        {"route": ["billing"], "route_confidence": 0.8},  # not a scalar
        {"route": "x" * (MAX_ANSWER + 1), "route_confidence": 0.8},  # a document, not an answer
        {"route": "  ", "route_confidence": 0.8},
        {"Route": "billing", "Route_confidence": 0.8},  # not a decision name
        {"_confidence": 0.8},
    ],
)
def test_what_is_not_an_answer_and_its_probability_is_not_recorded(outputs: dict[str, object]) -> None:
    assert declared_confidences(outputs) == []


def test_a_number_written_as_text_is_read_as_one() -> None:
    assert probability_of(" 0.25 ") == 0.25
    assert probability_of(1) == 1.0
    assert probability_of("1.5") is None
    assert declared_confidences({"done": "true", "done_confidence": "0.5"}) == [Answered("done", "true", 0.5)]


def test_a_stage_declares_a_confidence_by_its_outputs() -> None:
    assert declares_a_confidence({"confidence": 0.1})
    assert declares_a_confidence({"route_confidence": 0.1})
    assert not declares_a_confidence({"route": "billing"})
    assert not declares_a_confidence(None)


# ───────────────────────── when a decision is right ─────────────────────────


@pytest.mark.parametrize(
    ("overridden", "terminal", "reopened", "observed", "right"),
    [
        (None, None, False, False, None),  # nothing known yet
        (False, None, False, False, None),  # a person agreed, the work item has not ended
        (True, None, False, True, False),  # a person contradicted it: wrong, whatever follows
        (None, "success", False, True, True),
        (False, "success", False, True, True),
        (True, "success", False, True, False),
        (None, "declined", False, True, False),
        (None, "cancelled", False, True, False),
        (None, "success", True, True, False),  # it came back: the success was not one
    ],
)
def test_a_decision_is_right_when_nobody_contradicted_it_and_its_work_item_succeeded(
    overridden: bool | None, terminal: str | None, reopened: bool, observed: bool, right: bool | None
) -> None:
    assert is_observed(overridden, terminal, reopened) is observed
    assert is_right(overridden, terminal, reopened) is right


# ───────────────────────── the validator's warning ─────────────────────────

WORKFLOW = """
apiVersion: choregos/v1
kind: Workflow
metadata: {{ name: t, version: 1 }}
actors:
  router:
    type: decision
    role: route_ticket
    state: [title]
    questions:
      route: {{ type: choice, instructions: 'Which team?', options: [billing, tech] }}
  coder: {{ type: agent, role: implement }}
  platform: {{ type: system }}
  lead: {{ type: human, group: leads }}
states:
  inbox: {{ display: Inbox }}
  routed: {{ display: Routed }}
  billing: {{ display: Billing, terminal: true }}
  triage: {{ display: Triage, kind: wait }}
  done: {{ display: Done, terminal: true }}
transitions:
  - {{ id: t-route, from: inbox, to: routed, by: router }}
  - {{ id: t-billing, from: routed, to: billing, by: platform, when: {when} }}
{default}
  - {{ id: t-decide, from: triage, to: done, by: lead }}
  - {{ id: t-escalate, from: inbox, to: triage, by: lead }}
"""
TO_A_PERSON = "  - { id: t-triage, from: routed, to: triage, by: platform }"
TO_THE_END = "  - { id: t-triage, from: routed, to: done, by: platform }"
A_PERSON_DECIDES = "  - { id: t-triage, from: routed, to: done, by: lead }"
TO_AN_AGENT = "  - { id: t-triage, from: routed, to: done, by: coder }"
SURE = "{ route: billing, route_confidence: { gte: 0.8 } }"


def _warnings(default: str, when: str = SURE) -> list[tuple[str, str]]:
    _, report = parse_workflow(WORKFLOW.format(when=when, default=default), strict=False)
    assert report.valid, [issue.to_dict() for issue in report.errors]
    return [(issue.code, issue.path) for issue in report.warnings if issue.code.startswith("decision.")]


def test_a_threshold_whose_default_exit_leads_to_a_person_is_not_warned() -> None:
    assert _warnings(TO_A_PERSON) == []


def test_a_threshold_whose_default_exit_is_a_person_s_step_is_not_warned() -> None:
    assert _warnings(A_PERSON_DECIDES) == []


def test_a_threshold_whose_default_exit_reaches_no_person_is_warned() -> None:
    """Below 0.8 the work item ends `done` with nobody looking: the unsure answers go unchecked."""
    assert _warnings(TO_THE_END) == [
        ("decision.threshold_without_human", "transitions[1].when.route_confidence")
    ]


def test_a_default_exit_to_an_agent_step_is_no_person() -> None:
    assert _warnings(TO_AN_AGENT) == [
        ("decision.threshold_without_human", "transitions[1].when.route_confidence")
    ]


def test_a_branch_on_the_answer_alone_reads_no_probability() -> None:
    assert _warnings(TO_THE_END, when="{ route: billing }") == []


def test_the_warning_says_what_to_do() -> None:
    _, report = parse_workflow(WORKFLOW.format(when=SURE, default=TO_THE_END), strict=False)
    (warning,) = [w for w in report.warnings if w.code == "decision.threshold_without_human"]
    assert "`routed` branches on `route_confidence`" in warning.message
    assert "default exit `t-triage` goes to `done`" in warning.message
    assert "human step" in warning.message
