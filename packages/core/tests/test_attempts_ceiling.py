"""The policy's attempt ceilings and stage cap (S27-08)."""

from __future__ import annotations

from choregos_contracts import Policy, StageResult, StageStatus
from choregos_core import PolicyEngine, WorkflowEngine, load_template, policy_warnings


def test_a_ceiling_only_lowers_max_attempts() -> None:
    engine = WorkflowEngine(load_template("default-simple"))
    t = engine.select_transition("ready")  # t-implement: max_attempts 2
    failed = StageResult(status=StageStatus.FAILED, summary="red", reason="agent_error")
    assert engine.after_stage(t, failed, attempts=1).retried
    capped = engine.after_stage(t, failed, attempts=1, ceiling=1)
    assert capped.escalated and capped.next_state == "needs_human"
    assert engine.after_stage(t, failed, attempts=1, ceiling=5).retried, "a ceiling never raises it"


def _policy(attempts: dict[str, object]) -> Policy:
    return Policy.model_validate({"metadata": {"name": "p", "version": 1}, "attempts": attempts})


def test_by_role_is_the_ceiling_and_default_max_is_not() -> None:
    policy = PolicyEngine(_policy({"default_max": 2, "by_role": {"implement": 3}}))
    assert policy.attempts_ceilings() == {"implement": 3}
    assert policy.max_stages_per_ticket() == 50


def test_writing_default_max_is_warned() -> None:
    assert [i.path for i in policy_warnings(_policy({"default_max": 2}))] == ["attempts.default_max"]
    assert policy_warnings(_policy({"by_role": {"implement": 3}})) == []
