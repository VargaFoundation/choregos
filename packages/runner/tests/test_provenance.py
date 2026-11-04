"""What the platform measured, and what the agent declared (ADR 0045, S25-03).

Measured evidence overrides declared evidence (`merge_evidence`), but the merged result no longer said
which ones were: the console displayed "tests: ✓ 412" the same way, whether the runner
executed them or the agent wrote it. `Evidence.measured` says it, field by field.
"""

from __future__ import annotations

from choregos_contracts import Evidence
from choregos_runner.dod import merge_evidence


def test_the_merge_says_what_is_measured_field_by_field() -> None:
    declared = Evidence(
        tests_passed=True,
        tests_run=12,
        coverage_delta=1.5,
        facts={"report_attached": True, "finding_resolved": True},
    )
    measured = Evidence(tests_passed=False, tests_run=3, lint="ok", facts={"finding_resolved": False})
    merged = merge_evidence(declared, measured)
    assert merged.measured == ["facts.finding_resolved", "lint", "tests_passed", "tests_run"]
    # Coverage and the attached report come from the agent's account: they stay, unmeasured.
    assert merged.coverage_delta == 1.5
    assert merged.facts == {"report_attached": True, "finding_resolved": False}


def test_an_agent_cannot_call_itself_measured() -> None:
    declared = Evidence(tests_passed=True, tests_run=40, measured=["tests_passed", "tests_run"])
    assert merge_evidence(declared, Evidence()).measured == []


def test_nothing_measured_everything_is_declared() -> None:
    """An empty list, not `None`: `None` would mean "a runner too old to say so"."""
    assert merge_evidence(Evidence(tests_run=3), Evidence()).measured == []
