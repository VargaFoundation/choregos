"""Moteur du DSL : sélection déterministe et suites d'une étape."""

from __future__ import annotations

import pytest
from choregos_contracts import Evidence, StageResult, StageStatus
from choregos_core import GateOutcome, WorkflowEngine, load_template


@pytest.fixture
def simple() -> WorkflowEngine:
    return WorkflowEngine(load_template("default-simple"))


@pytest.fixture
def auto() -> WorkflowEngine:
    return WorkflowEngine(load_template("full-auto"))


def done(summary: str = "fait") -> StageResult:
    return StageResult(
        status=StageStatus.DONE,
        summary=summary,
        evidence=Evidence(tests_passed=True, tests_run=10),
    )


def test_select_transition_is_deterministic(simple: WorkflowEngine) -> None:
    for _ in range(5):
        assert simple.select_transition("inbox").id == "t-refine"
        assert simple.select_transition("ready").id == "t-implement"
        assert simple.select_transition("pr_open").id == "t-merge"


def test_select_transition_prefers_explicit_outcome(auto: WorkflowEngine) -> None:
    assert auto.select_transition("pr_open").id == "t-merge"
    assert auto.select_transition("pr_open", "ci_failed").id == "t-fix-ci"
    assert auto.select_transition("pr_open", "t-fix-ci").id == "t-fix-ci"


def test_select_transition_skips_repair_roles_by_default(auto: WorkflowEngine) -> None:
    """`fix_ci` et `address_review` ne se déclenchent que sur un résultat explicite."""
    transition = auto.select_transition("pr_open")
    assert auto.agent_of(transition) is None  # t-merge est porté par le système


def test_select_transition_none_on_terminal(simple: WorkflowEngine) -> None:
    assert simple.select_transition("deployed_prod") is None
    assert simple.is_terminal("deployed_prod")


def test_actor_kinds(simple: WorkflowEngine) -> None:
    assert simple.actor_kind(simple.select_transition("inbox")) == "agent"
    assert simple.actor_kind(simple.select_transition("awaiting_spec_approval")) == "human"
    assert simple.actor_kind(simple.select_transition("verifying")) == "system"
    assert simple.actor_kind(simple.select_transition("merged")) == "train"


def test_after_stage_moves_forward(simple: WorkflowEngine) -> None:
    t = simple.select_transition("ready")
    decision = simple.after_stage(t, done(), attempts=0, gate_outcomes=[GateOutcome("scope_respected", True)])
    assert decision.next_state == "in_progress"
    assert not decision.retried


def test_after_stage_retries_then_escalates(simple: WorkflowEngine) -> None:
    t = simple.select_transition("ready")
    failed = StageResult(status=StageStatus.FAILED, summary="crash", reason="agent_error")
    first = simple.after_stage(t, failed, attempts=0)
    assert first.next_state == "ready" and first.retried
    second = simple.after_stage(t, failed, attempts=1)
    assert second.next_state == "ready" and second.retried
    third = simple.after_stage(t, failed, attempts=2)
    assert third.next_state == "needs_human" and third.escalated


def test_after_stage_blocking_gate_retries(simple: WorkflowEngine) -> None:
    t = simple.select_transition("ready")
    decision = simple.after_stage(
        t, done(), attempts=0, gate_outcomes=[GateOutcome("scope_respected", False, detail="2 fichiers")]
    )
    assert decision.retried
    assert "scope_respected" in decision.reason


def test_after_stage_pending_gate_waits(simple: WorkflowEngine) -> None:
    t = simple.select_transition("ready")
    decision = simple.after_stage(
        t, done(), attempts=0, gate_outcomes=[GateOutcome("ci_green", False, pending=True)]
    )
    assert decision.next_state == "ready"
    assert "attente" in decision.reason


def test_needs_human_uses_defaults(simple: WorkflowEngine) -> None:
    t = simple.select_transition("ready")
    result = StageResult(status=StageStatus.NEEDS_HUMAN, summary="question", reason="question")
    assert simple.after_stage(t, result, attempts=0).next_state == "needs_human"


def test_budget_exceeded_routes_to_default_state(simple: WorkflowEngine) -> None:
    t = simple.select_transition("ready")
    result = StageResult(status=StageStatus.FAILED, summary="budget", reason="budget")
    assert simple.after_stage(t, result, attempts=0).next_state == "needs_human"


def test_after_human_approval_and_rejection(simple: WorkflowEngine) -> None:
    t = simple.select_transition("awaiting_spec_approval")
    assert simple.after_human(t, approved=True).next_state == "ready"
    assert simple.after_human(t, approved=False).next_state == "refining"


def test_after_train(simple: WorkflowEngine) -> None:
    t = simple.select_transition("merged")
    assert simple.after_train(t, ok=True).next_state == "deployed_prod"
    rollback = simple.after_train(t, ok=False)
    assert rollback.escalated and rollback.next_state == "needs_human"


def test_migration_requires_known_state(simple: WorkflowEngine) -> None:
    other = load_template("advanced")
    assert simple.can_migrate_to(other, "in_progress") == "in_progress"
    with pytest.raises(ValueError, match="n'existe pas"):
        simple.can_migrate_to(other, "deployed_prod_only_here")
    assert (
        simple.can_migrate_to(other, "deployed_prod", {"deployed_prod": "verified_prod"}) == "verified_prod"
    )


def test_max_attempts(simple: WorkflowEngine) -> None:
    assert simple.max_attempts(simple.select_transition("ready")) == 2
    assert simple.max_attempts(simple.select_transition("pr_open")) == 3
