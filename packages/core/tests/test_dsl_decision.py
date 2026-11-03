# SPDX-License-Identifier: Apache-2.0
"""A decision step in the DSL (ADR 0120): recognised by the engine, refused until it runs (S52-04)."""

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


def test_a_workflow_that_runs_a_decision_step_is_refused_until_the_engine_runs_it() -> None:
    assert "decision.not_runnable" in _codes()


def test_a_decision_declared_and_not_used_is_not_refused() -> None:
    assert "decision.not_runnable" not in _codes(transitions=UNUSED)


def test_a_question_named_like_another_s_probability_is_refused() -> None:
    clash = QUESTIONS + "      route_confidence: { type: yes_no, instructions: 'Sure?' }\n"
    assert "decision.output_collision" in _codes(clash, UNUSED)
    assert "decision.output_collision" not in _codes(transitions=UNUSED)
