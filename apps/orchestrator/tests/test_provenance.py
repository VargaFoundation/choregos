"""Provenance is read on the PR (#283): a check whose name contains `provenance`."""

from __future__ import annotations

from choregos_core.domain import CheckRun


def test_provenance_is_read_in_the_checks_of_the_pr() -> None:
    from choregos_orchestrator.activities.gates import _provenance

    def check(check_name: str, conclusion: str | None) -> CheckRun:
        status = "completed" if conclusion else "in_progress"
        return CheckRun(name=check_name, status=status, conclusion=conclusion)

    assert _provenance([check("ci", "success")]) is None, "no provenance check: unknown"
    assert _provenance([check("attest-build-provenance", None)]) is None, "not finished yet"
    assert _provenance([check("attest-build-provenance", "success")]) is True
    assert _provenance([check("SLSA Provenance", "failure"), check("ci", "success")]) is False
