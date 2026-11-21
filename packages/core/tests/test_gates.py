"""Each gate of the core has a green test and a red test (S1-05); those of software delivery are the
`software-delivery` plugin's since ADR 0181 round C2 (`plugins/software-delivery/tests/test_gates.py`)."""

from __future__ import annotations

import pytest
from tranibor_contracts import Evidence, StageResult, StageStatus
from tranibor_core import (
    GateContext,
    GateError,
    evaluate,
    is_async_gate,
    known_gates,
    matches_any,
    scan_secrets,
)


def result(**evidence: object) -> StageResult:
    return StageResult(status=StageStatus.DONE, summary="ok", evidence=Evidence(**evidence))  # type: ignore[arg-type]


def test_registry_is_complete() -> None:
    expected = {"evidence_present", "external", "flag_present", "outputs_present", "action_succeeded"}
    assert expected <= set(known_gates())
    assert is_async_gate("external")
    assert not is_async_gate("evidence_present")


def test_unknown_gate_raises() -> None:
    with pytest.raises(GateError, match="unknown gate"):
        evaluate("licorne", GateContext())


def test_evidence_present() -> None:
    ctx = GateContext(result=result(tests_passed=True, tests_run=12))
    assert evaluate("evidence_present", ctx).passed
    assert not evaluate("evidence_present", GateContext()).passed
    assert not evaluate(
        "evidence_present", GateContext(result=result(tests_passed=False, tests_run=1))
    ).passed
    partial = GateContext(result=result(tests_passed=True, tests_run=1))
    assert not evaluate("evidence_present", partial, {"require_lint": True}).passed


def test_scan_secrets_detects_common_shapes() -> None:
    assert scan_secrets("AKIA1234567890ABCDEF") == ["AWS key"]
    assert scan_secrets("-----BEGIN PRIVATE KEY-----") == ["private key"]
    assert scan_secrets("ghp_" + "a" * 30) == ["GitHub token"]
    assert scan_secrets("nothing to see") == []


def test_flag_present() -> None:
    assert evaluate("flag_present", GateContext(flags=["new-billing"]), {"name": "new-billing"}).passed
    out = evaluate("flag_present", GateContext(flags=[]), {"name": "new-billing"})
    assert out.blocking and "missing" in out.detail
    assert evaluate("flag_present", GateContext()).blocking


def test_external() -> None:
    assert evaluate("external", GateContext(), {"url": "https://x"}).pending
    assert evaluate(
        "external", GateContext(external_results={"https://x": True}), {"url": "https://x"}
    ).passed


def test_matches_any_globs() -> None:
    assert matches_any("src/orders/a.py", ["src/orders/**"])
    assert matches_any("src/orders", ["src/orders/**"])
    assert matches_any("src/a.py", ["src/*.py"])
    assert not matches_any("tests/a.py", ["src/**"])


def test_outputs_present_is_the_gate_of_trades_without_tests() -> None:
    """A non-software workflow has neither tests nor a diff: its mechanical guarantee is that
    the stage did produce what the transition declares."""
    from tranibor_contracts import StageResult, StageStatus

    done = StageResult(
        schema="choregos/StageResult/v1",
        status=StageStatus.DONE,
        summary="three candidates",
        outputs={"shortlist": "3 profiles", "notes": "interviews scheduled"},
    )
    ctx = GateContext(result=done, expected_outputs=["shortlist", "notes"])
    assert evaluate("outputs_present", ctx).passed

    lacking = GateContext(result=done, expected_outputs=["shortlist", "report"])
    verdict = evaluate("outputs_present", lacking)
    assert not verdict.passed
    assert "report" in verdict.detail

    assert not evaluate("outputs_present", GateContext(expected_outputs=["x"])).passed


def test_a_guarantee_with_nothing_to_check_refuses() -> None:
    """Shipped on 2026-09-23, `outputs_present` PASSED when the transition declared
    no output: exactly the defect just fixed on the diff guarantees.
    A requested guarantee that looked at nothing must refuse; tolerating it is said."""
    nothing = GateContext()
    verdict = evaluate("outputs_present", nothing)
    assert not verdict.passed
    assert "no output declared" in verdict.detail
    assert evaluate("outputs_present", nothing, {"allow_empty": True}).passed

    assert not evaluate("evidence_facts", GateContext()).passed


def test_evidence_facts_reads_the_trades_evidence() -> None:
    """`evidence_present` requires tests, `outputs_present` believes what the agent tells.
    Between the two: facts named by the trade, and comparable."""
    from tranibor_contracts import Evidence, StageResult, StageStatus

    def make_result(**facts: object) -> StageResult:
        return StageResult(
            schema="choregos/StageResult/v1",
            status=StageStatus.DONE,
            summary="investigated file",
            evidence=Evidence(facts=dict(facts)),  # type: ignore[arg-type]
        )

    ok = GateContext(result=make_result(profiles_kept=3, identity_document=True))
    params = {"keys": ["profiles_kept", "identity_document"], "min": {"profiles_kept": 1}}
    assert evaluate("evidence_facts", ok, params).passed

    absent = GateContext(result=make_result(identity_document=True))
    assert "profiles_kept" in evaluate("evidence_facts", absent, params).detail

    zero = GateContext(result=make_result(profiles_kept=0, identity_document=True))
    verdict = evaluate("evidence_facts", zero, params)
    assert not verdict.passed, "a polite zero is not a result"
    assert "profiles_kept = 0 < 1" in verdict.detail

    # A boolean IS an integer in Python: without a guard, `false` would pass a `min: 0`.
    not_true = GateContext(result=make_result(profiles_kept=2, identity_document=False))
    assert not evaluate(
        "evidence_facts", not_true, {"keys": ["identity_document"], "must_be_true": ["identity_document"]}
    ).passed


def test_evidence_facts_max_is_a_ceiling_on_numbers_only() -> None:
    from tranibor_contracts import Evidence, StageResult, StageStatus

    def make_result(**facts: object) -> StageResult:
        return StageResult(
            schema="choregos/StageResult/v1",
            status=StageStatus.DONE,
            summary="facts",
            evidence=Evidence(facts=dict(facts)),  # type: ignore[arg-type]
        )

    params = {"keys": ["diverging_excess"], "max": {"diverging_excess": 0}}
    assert evaluate("evidence_facts", GateContext(result=make_result(diverging_excess=0)), params).passed
    verdict = evaluate("evidence_facts", GateContext(result=make_result(diverging_excess=2)), params)
    assert not verdict.passed and "diverging_excess = 2 > 0" in verdict.detail
    # A bool is an int in Python: a declared `true` is not a number over a ceiling of 0.
    assert evaluate("evidence_facts", GateContext(result=make_result(diverging_excess=True)), params).passed
    # `max` and `min` together bound a fact from both sides.
    both = {"keys": ["n"], "min": {"n": 1}, "max": {"n": 3}}
    assert evaluate("evidence_facts", GateContext(result=make_result(n=2)), both).passed
    assert not evaluate("evidence_facts", GateContext(result=make_result(n=4)), both).passed
    assert not evaluate("evidence_facts", GateContext(result=make_result(n=0)), both).passed


def test_tool_called_reads_the_ledger_not_the_narrative() -> None:
    """Bench of 2026-09-24: ten catalogue listings, zero calls, and `place_verified: true`
    recorded anyway. The guarantee looks at what the platform counted."""
    from tranibor_contracts import StageResult, StageStatus

    told = StageResult(
        schema="choregos/StageResult/v1",
        status=StageStatus.DONE,
        summary="place verified",
        evidence={"facts": {"place_verified": True}},
    )
    without_call = GateContext(result=told, tool_calls=[])
    verdict = evaluate("tool_called", without_call, {"tools": ["verify_address"]})
    assert not verdict.passed
    assert "verify_address" in verdict.detail and verdict.annotations == ["verify_address"]

    with_call = GateContext(result=told, tool_calls=["verify_address", "other"])
    assert evaluate("tool_called", with_call, {"tools": ["verify_address"]}).passed

    # requested without requiring anything: it refuses, like the other guarantees with nothing to check
    assert not evaluate("tool_called", with_call).passed


# ───────────────────────── outputs_in and markdown_sections (S21-20) ─────────────────────────


def _with_outputs(**outputs: object) -> StageResult:
    return StageResult(status=StageStatus.DONE, summary="ok", outputs=outputs)  # type: ignore[arg-type]


def test_outputs_in_refuses_a_review_that_asks_for_changes() -> None:
    allowed = {"values": {"verdict": ["approve"]}}
    assert evaluate("outputs_in", GateContext(result=_with_outputs(verdict="approve")), allowed).passed
    refusal = evaluate("outputs_in", GateContext(result=_with_outputs(verdict="request_changes")), allowed)
    assert refusal.blocking and "verdict = 'request_changes' (allowed: approve)" in refusal.detail
    assert evaluate("outputs_in", GateContext(result=_with_outputs()), allowed).blocking, "absent: refused"
    assert evaluate("outputs_in", GateContext(), allowed).blocking, "no result: refused"
    assert not evaluate("outputs_in", GateContext(result=_with_outputs(verdict="approve")), {}).passed


def test_outputs_in_reads_several_outputs_together() -> None:
    allowed = {"values": {"size": ["S", "M"], "risk": ["low", "medium"]}}
    assert evaluate("outputs_in", GateContext(result=_with_outputs(size="M", risk="low")), allowed).passed
    too_big = evaluate("outputs_in", GateContext(result=_with_outputs(size="L", risk="low")), allowed)
    assert too_big.annotations == ["size = 'L' (allowed: S, M)"]


# ───────────────────────── adr_number_free (#287, S22-20) ─────────────────────────
