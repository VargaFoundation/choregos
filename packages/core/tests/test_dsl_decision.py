# SPDX-License-Identifier: Apache-2.0
"""A decision step in the DSL (ADR 0121): known to the engine, valid in a workflow, branched on by `when`."""

from __future__ import annotations

from choregos_core import parse_workflow
from choregos_core.dsl.engine import WorkflowEngine

WORKFLOW = """
apiVersion: choregos/v1
kind: Workflow
metadata: {{ name: t, version: 1 }}
actors:
  router:
    type: decision
    role: route_ticket
    state: [title, description]
    questions:
{questions}
  owner: {{ type: human, group: po }}
states:
  inbox: {{ display: Inbox }}
  review: {{ display: Review }}
  done: {{ display: Done, terminal: true }}
transitions:
{transitions}
"""
QUESTIONS = (
    "      route: { type: choice, instructions: 'Which team?', options: [billing, tech] }\n"
    "      urgent: { type: yes_no, instructions: 'Today?' }\n"
)
USED = "  - {id: t1, from: inbox, to: review, by: router}\n  - {id: t2, from: review, to: done, by: owner}\n"
UNUSED = "  - {id: t1, from: inbox, to: review, by: owner}\n  - {id: t2, from: review, to: done, by: owner}\n"


def _codes(questions: str = QUESTIONS, transitions: str = USED) -> list[str]:
    _, report = parse_workflow(WORKFLOW.format(questions=questions, transitions=transitions), strict=False)
    return [issue.code for issue in report.errors]


def test_the_engine_knows_a_decision_step_for_what_it_is() -> None:
    """Without its own kind the interpreter would take it for a transition with nothing to run."""
    workflow, _ = parse_workflow(WORKFLOW.format(questions=QUESTIONS, transitions=USED), strict=False)
    engine = WorkflowEngine(workflow)
    assert engine.actor_kind(workflow.transitions[0]) == "decision"
    assert engine.agent_of(workflow.transitions[0]) is None
    assert engine.job_of(workflow.transitions[0]) is None


def test_a_workflow_that_runs_a_decision_step_is_valid() -> None:
    """S52-04 runs it: the refusal S52-01 put in place until then (`decision.not_runnable`) is gone."""
    assert _codes() == []
    assert _codes(transitions=UNUSED) == []


def test_the_engine_names_the_decision_a_step_asks() -> None:
    workflow, _ = parse_workflow(WORKFLOW.format(questions=QUESTIONS, transitions=USED), strict=False)
    engine = WorkflowEngine(workflow)
    decision = engine.decision_of(workflow.transitions[0])
    assert decision is not None
    assert decision.outputs == ["route", "route_confidence", "urgent", "urgent_confidence"]
    assert engine.decision_of(workflow.transitions[1]) is None


BRANCHES = (
    USED
    + "  - {id: t3, from: review, to: done, by: owner, "
    + "when: {route: billing, route_confidence: {gte: 0.8}}}\n"
)


def test_when_reads_a_decision_s_answer_and_its_probability() -> None:
    """`route` and `route_confidence` are declared by the decision actor, not by a transition's
    `outputs`: without counting them, `when` on them was `when.unknown_output`."""
    assert _codes(transitions=BRANCHES) == []


def test_when_still_refuses_an_output_nothing_writes() -> None:
    typo = BRANCHES.replace("route_confidence: {gte", "route_confidance: {gte")
    assert "when.unknown_output" in _codes(transitions=typo)


def test_an_unused_decision_declares_no_output() -> None:
    """Only a decision a transition asks writes its outputs: an actor nobody uses writes nothing."""
    unused = UNUSED + "  - {id: t3, from: review, to: done, by: owner, when: {route: billing}}\n"
    assert "when.unknown_output" in _codes(transitions=unused)


def test_a_question_named_like_another_s_probability_is_refused() -> None:
    clash = QUESTIONS + "      route_confidence: { type: yes_no, instructions: 'Sure?' }\n"
    assert "decision.output_collision" in _codes(clash, UNUSED)
    assert "decision.output_collision" not in _codes(transitions=UNUSED)
