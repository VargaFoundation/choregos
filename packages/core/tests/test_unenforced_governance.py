"""Governance the platform does not enforce is refused when written (ADR 0101)."""

from __future__ import annotations

from pathlib import Path

from choregos_core import parse_policy, parse_workflow
from choregos_core.dsl import TEMPLATE_NAMES, load_template
from choregos_core.dsl.validator import validate_workflow
from choregos_core.policy import UNENFORCED_GOVERNANCE_CODES, unenforced_governance

ROOT = Path(__file__).resolve().parents[3]


def _workflow(review: str, *, extra_actor: str = "", by: str = "r") -> str:
    return f"""
apiVersion: choregos/v1
kind: Workflow
metadata: {{ name: w, version: 1 }}
actors:
  r: {{ type: agent, role: review }}
{extra_actor}
states:
  todo: {{ display: To do }}
  done: {{ display: Done, terminal: true }}
transitions:
  - id: t-review
    from: todo
    to: done
    by: {by}
    review: {review}
"""


def _codes(text: str) -> tuple[list[str], list[str]]:
    _, report = parse_workflow(text, strict=False)
    return [i.code for i in report.errors], [i.code for i in report.warnings]


def test_review_humans_is_an_error() -> None:
    errors, _ = _codes(_workflow("{ agents: [r], humans: { group: g, required: always } }"))
    assert "review.humans_not_enforced" in errors


def test_required_never_is_not_an_error() -> None:
    errors, _ = _codes(_workflow("{ agents: [r], humans: { group: g, required: never } }"))
    assert "review.humans_not_enforced" not in errors


def test_a_review_agent_that_never_runs_is_an_error() -> None:
    text = _workflow(
        "{ agents: [security] }",
        extra_actor="  security: { type: agent, role: security }",
    )
    errors, _ = _codes(text)
    assert "review.agent_not_run" in errors


def test_review_agents_no_longer_hide_an_unused_actor() -> None:
    text = _workflow(
        "{ agents: [security] }",
        extra_actor="  security: { type: agent, role: security }",
    )
    errors, warnings = _codes(text)
    assert "review.agent_not_run" in errors
    assert "actor.unused" in warnings


def test_the_shipped_templates_validate() -> None:
    for name in TEMPLATE_NAMES:
        report = validate_workflow(load_template(name))
        assert report.errors == [], (name, [i.code for i in report.errors])
    files = sorted((ROOT / "templates").glob("*/workflows/*.yaml"))
    assert files
    for path in files:
        _, report = parse_workflow(path.read_text(encoding="utf-8"), strict=False)
        assert report.errors == [], (path.name, [i.code for i in report.errors])


def test_advanced_is_version_3_and_promises_no_human_review() -> None:
    advanced = load_template("advanced")
    assert advanced.metadata.version == 3
    assert all(t.review is None or t.review.humans is None for t in advanced.transitions)


def test_unenforced_governance_lists_only_the_three_codes() -> None:
    policy = parse_policy(
        """
apiVersion: choregos/v1
kind: Policy
metadata: { name: p, version: 1 }
approvals:
  merge: { required: always, group: maintainers }
sandbox:
  network: { deny_by_default: false }
"""
    )
    assert [i.code for i in unenforced_governance(policy)] == ["policy.approval_not_enforced"]
    assert "policy.network_deny_by_default_not_enforced" not in UNENFORCED_GOVERNANCE_CODES
