"""Policy engine : budgets, approbations, tentatives, périmètre (couverture visée : 100 %)."""

from __future__ import annotations

import pytest
from choregos_contracts import HumanRequestKind, Policy, Risk, Size
from choregos_core import PolicyEngine, ValidationError, engine_for, load_preset, parse_policy, preset_yaml
from choregos_core.policy import PRESET_NAMES


@pytest.mark.parametrize("name", PRESET_NAMES)
def test_presets_parse(name: str) -> None:
    policy = load_preset(name)
    assert policy.metadata.name == name
    assert preset_yaml(name).startswith("#")


def test_budget_for_by_role_and_size() -> None:
    e = PolicyEngine(load_preset("solo"))
    assert e.stage_usd("implement", Size.S) == 5
    assert e.stage_usd("implement", Size.XL) == 45
    assert e.stage_usd("role_inconnu", Size.M) == 8  # retombe sur `default`
    budget = e.budget_for("implement", Size.L)
    assert budget.usd == 25
    assert budget.max_turns == 120
    assert budget.max_minutes == 90


def test_budget_turns_factor() -> None:
    e = PolicyEngine(load_preset("solo"))
    assert e.budget_for("implement", Size.XL, turns_factor=1.5).max_turns == 180


def test_ticket_budget_and_alerts() -> None:
    e = PolicyEngine(load_preset("solo"))
    assert e.budget_ticket(Size.M) == 25
    assert e.budget_ticket(None) == 25
    assert not e.over_ticket_budget(24, Size.M)
    assert e.over_ticket_budget(26, Size.M)
    assert e.should_alert(20, Size.M)
    assert not e.should_alert(19, Size.M)
    assert e.daily_budget() == 150


def test_approvals_always_never_by_size_by_risk() -> None:
    e = PolicyEngine(load_preset("solo"))
    assert e.approval_for("prod").required
    assert not e.approval_for("merge").required
    assert e.approval_for(HumanRequestKind.APPROVAL, size=Size.M).required
    assert not e.approval_for(HumanRequestKind.APPROVAL, size=Size.S).required
    team = PolicyEngine(load_preset("team"))
    assert team.approval_for("merge", risk=Risk.HIGH).required
    assert not team.approval_for("merge", risk=Risk.LOW).required
    assert team.approval_for("prod").group == "release-captains"


def test_approval_without_rule_is_not_required() -> None:
    policy = Policy.model_validate(
        {"apiVersion": "choregos/v1", "kind": "Policy", "metadata": {"name": "p", "version": 1}}
    )
    e = PolicyEngine(policy)
    assert not e.approval_for("prod").required
    assert e.budget_ticket(Size.L) == 60  # défauts de la plateforme
    assert e.stage_usd("implement", Size.S) == 3


def test_attempts_and_dod() -> None:
    e = PolicyEngine(load_preset("solo"))
    assert e.attempts_for("implement") == 3
    assert e.attempts_for("verify") == 2
    assert e.dod_iterations() == 3


def test_scope_auto_grant() -> None:
    e = PolicyEngine(load_preset("solo"))
    assert e.scope_auto_grant(["src/a.py", "src/b.py"])
    assert not e.scope_auto_grant(["a.py", "b.py", "c.py", "d.py"])
    assert not e.scope_auto_grant([".choregos/workflow.yaml"])
    assert e.path_denied(".github/workflows/ci.yml")
    assert not e.path_denied("src/app.py")
    assert e.max_diff() == (60, 3000)
    assert "kubectl" in e.deny_commands()
    assert "github.com" in e.allow_domains()


def test_findings_and_memory() -> None:
    e = PolicyEngine(load_preset("solo"))
    assert e.max_findings_per_run() == 5
    assert e.dedupe_threshold() == 0.86
    assert e.auto_agent_ready(Size.S)
    assert not e.auto_agent_ready(Size.L)
    assert e.notify_finding("critical")
    assert not e.notify_finding("low")
    assert e.memory_enabled()
    assert e.memory_budget("implement") == 4000
    assert e.memory_budget("triage") == 2000


def test_memory_disabled_gives_zero_budget() -> None:
    policy = load_preset("solo").model_copy(deep=True)
    policy.memory.enabled = False
    assert PolicyEngine(policy).memory_budget("implement") == 0


def test_review_policy() -> None:
    team = PolicyEngine(load_preset("team"))
    assert team.cross_backend_review()
    assert team.human_review_required(Risk.MEDIUM)
    solo = PolicyEngine(load_preset("solo"))
    assert not solo.cross_backend_review()
    assert not solo.human_review_required(Risk.LOW)


def test_train_config() -> None:
    e = PolicyEngine(load_preset("team"))
    assert set(e.train_envs()) == {"dev", "staging", "prod"}
    prod = e.train("prod")
    assert prod is not None and prod.batch_max == 8
    assert prod.approval is not None and prod.approval.required
    assert e.train("inconnu") is None


def test_engine_for_accepts_preset_yaml_and_object() -> None:
    assert engine_for("preset:team").policy.metadata.name == "team"
    assert engine_for(preset_yaml("solo")).policy.metadata.name == "solo"
    assert engine_for(load_preset("solo")).policy.metadata.name == "solo"


def test_parse_policy_rejects_garbage() -> None:
    with pytest.raises(ValidationError, match="politique"):
        parse_policy("- juste une liste")
    with pytest.raises(ValidationError):
        parse_policy("apiVersion: choregos/v1\nkind: Policy\nmetadata: {name: X, version: 0}\n")
    with pytest.raises(ValidationError):
        parse_policy("a: [oups\n")


def test_unknown_preset_raises() -> None:
    with pytest.raises(FileNotFoundError, match="preset de politique inconnu"):
        load_preset("licorne")
