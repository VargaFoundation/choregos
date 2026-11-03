"""Policy engine: budgets, attempts, perimeter, trains (targeted coverage: 100%)."""

from __future__ import annotations

import pathlib

import pytest
import yaml
from choregos_contracts import Policy, Size
from choregos_core import (
    PolicyEngine,
    ValidationError,
    engine_for,
    load_preset,
    parse_policy,
    policy_warnings,
    preset_yaml,
)
from choregos_core.policy import PRESET_NAMES

ROOT = pathlib.Path(__file__).resolve().parents[3]
TEMPLATE_POLICIES = sorted((ROOT / "templates").glob("*/policy.yaml"))


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


def test_policy_without_sections_falls_back_on_platform_defaults() -> None:
    policy = Policy.model_validate(
        {"apiVersion": "choregos/v1", "kind": "Policy", "metadata": {"name": "p", "version": 1}}
    )
    e = PolicyEngine(policy)
    assert e.budget_ticket(Size.L) == 60  # the platform's defaults
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
    assert PolicyEngine(load_preset("team")).cross_backend_review()
    assert not PolicyEngine(load_preset("solo")).cross_backend_review()


# ───────────── approvals and human review: declared in the workflow (ADR 0044) ─────────────

#: A policy from before ADR 0044, as the presets wrote it: it stays valid.
POLICY_BEFORE_ADR_0044 = """
apiVersion: choregos/v1
kind: Policy
metadata: { name: old, version: 3 }
approvals:
  spec: { required: by_size, group: product-owners, sizes: [M, L, XL], timeout_hours: 72 }
  merge: { required: never }
  prod: { required: always, group: release-captains, timeout_hours: 4 }
  scope_change: { required: by_risk, group: maintainers, risks: [high] }
review: { cross_backend: true, require_human_for_risk: [medium, high] }
"""


@pytest.mark.parametrize("name", PRESET_NAMES)
def test_a_preset_promises_neither_approval_nor_human_review(name: str) -> None:
    """The presets used to write "spec approval", "human review at merge", "prod:
    always": none of these guards exists. They no longer write them, and do not warn."""
    raw = yaml.safe_load(preset_yaml(name))
    assert "approvals" not in raw
    assert "require_human_for_risk" not in (raw.get("review") or {})
    assert policy_warnings(load_preset(name)) == []


@pytest.mark.parametrize("path", TEMPLATE_POLICIES, ids=lambda c: c.parent.name)
def test_a_template_policy_promises_nothing_either(path: pathlib.Path) -> None:
    text = path.read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    assert "approvals" not in raw
    assert "require_human_for_risk" not in (raw.get("review") or {})
    assert policy_warnings(parse_policy(text)) == []


def test_the_template_policies_are_found() -> None:
    """Without it, the parametrised test above would play no case and pass in silence."""
    assert any(c.parent.name == "github-software-delivery" for c in TEMPLATE_POLICIES)


def test_a_policy_that_fills_them_in_stays_valid_and_warns() -> None:
    """The contract does not change (ADR 0001): the policy loads, and each key that promises a
    guard is named, in English, with what to do instead."""
    policy = parse_policy(POLICY_BEFORE_ADR_0044)
    issues = policy_warnings(policy)
    assert [i.path for i in issues] == [
        "approvals.spec",
        "approvals.prod",
        "approvals.scope_change",
        "review.require_human_for_risk",
    ]
    assert {i.code for i in issues} == {"policy.approval_not_enforced", "policy.review_not_enforced"}
    for issue in issues:
        assert "is not enforced" in issue.message
        assert "Declare a human transition in the workflow" in issue.message
    # `required: never` promises nothing: no warning for `approvals.merge`.
    assert "approvals.merge" not in {i.path for i in issues}


def test_the_contract_default_does_not_warn() -> None:
    """`review.require_human_for_risk` is `[high]` by default in the contract: a policy that
    says nothing about it must not get a warning for a key it did not write."""
    policy = parse_policy("apiVersion: choregos/v1\nkind: Policy\nmetadata: {name: p, version: 1}\n")
    assert policy.review.require_human_for_risk  # the default is there...
    assert policy_warnings(policy) == []  # ... and it does not warn
    empty = parse_policy(
        "apiVersion: choregos/v1\nkind: Policy\nmetadata: {name: p, version: 1}\n"
        "review: {require_human_for_risk: []}\n"
    )
    assert policy_warnings(empty) == []


def test_the_engine_decides_no_approval() -> None:
    """`approval_for` and `human_review_required` were called by nobody: an engine that
    answers "approval required" when nothing asks for it is a phantom guard."""
    assert not hasattr(PolicyEngine, "approval_for")
    assert not hasattr(PolicyEngine, "human_review_required")


def test_train_config() -> None:
    e = PolicyEngine(load_preset("team"))
    assert set(e.train_envs()) == {"dev", "staging", "prod"}
    prod = e.train("prod")
    assert prod is not None and prod.batch_max == 8
    assert prod.approval is not None and prod.approval.required
    assert e.train("unknown") is None


def test_engine_for_accepts_preset_yaml_and_object() -> None:
    assert engine_for("preset:team").policy.metadata.name == "team"
    assert engine_for(preset_yaml("solo")).policy.metadata.name == "solo"
    assert engine_for(load_preset("solo")).policy.metadata.name == "solo"


def test_parse_policy_rejects_garbage() -> None:
    with pytest.raises(ValidationError, match="policy"):
        parse_policy("- just a list")
    with pytest.raises(ValidationError):
        parse_policy("apiVersion: choregos/v1\nkind: Policy\nmetadata: {name: X, version: 0}\n")
    with pytest.raises(ValidationError):
        parse_policy("a: [oops\n")


def test_unknown_preset_raises() -> None:
    with pytest.raises(FileNotFoundError, match="unknown policy preset"):
        load_preset("licorne")
