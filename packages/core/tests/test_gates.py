"""Each gate has a green test and a red test (S1-05)."""

from __future__ import annotations

import pytest
from choregos_contracts import Evidence, StageResult, StageStatus
from choregos_core import (
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
    expected = {
        "ci_green",
        "scans_ok",
        "evidence_present",
        "scope_respected",
        "review_approved",
        "coverage_delta_min",
        "diff_size_max",
        "no_secrets",
        "provenance_signed",
        "external",
        "flag_present",
    }
    assert expected <= set(known_gates())
    assert is_async_gate("ci_green")
    assert not is_async_gate("scope_respected")


def test_unknown_gate_raises() -> None:
    with pytest.raises(GateError, match="unknown gate"):
        evaluate("licorne", GateContext())


@pytest.mark.parametrize(
    ("changed", "allowed", "expected"),
    [
        (["src/a.py"], ["src/**"], True),
        (["src/orders/deep/x.py"], ["src/orders/**"], True),
        (["docs/x.md"], ["src/**"], False),
        (["src/a.py"], [], True),
    ],
)
def test_scope_respected(changed: list[str], allowed: list[str], expected: bool) -> None:
    out = evaluate("scope_respected", GateContext(changed_files=changed, allowed_paths=allowed))
    assert out.passed is expected


@pytest.mark.parametrize(
    ("changed", "allowed", "expected"),
    [
        ([".github/workflows/ci.yml", "src/a.py"], ["**"], False),
        (["src/generated/client.py"], ["src/**"], False),
        ([".github/workflows/ci.yml"], [], False),
        ([".github/CODEOWNERS", "src/a.py"], ["**"], True),
    ],
    ids=["denied-inside-the-scope", "denied-under-an-allowed-folder", "denied-without-a-scope", "beside"],
)
def test_scope_respected_fails_on_a_path_the_policy_denies(
    changed: list[str], allowed: list[str], expected: bool
) -> None:
    """`scope.deny_paths` wins over the allowed paths, and holds when none is declared (C2)."""
    denied = [".github/workflows/**", "src/generated/**"]
    out = evaluate(
        "scope_respected", GateContext(changed_files=changed, allowed_paths=allowed, denied_paths=denied)
    )
    assert out.passed is expected, out.detail
    if not expected:
        assert "scope.deny_paths" in out.detail
        assert list(out.annotations) == [
            p for p in changed if p.startswith((".github/workflows/", "src/generated/"))
        ]


def test_evidence_present() -> None:
    ctx = GateContext(result=result(tests_passed=True, tests_run=12))
    assert evaluate("evidence_present", ctx).passed
    assert not evaluate("evidence_present", GateContext()).passed
    assert not evaluate(
        "evidence_present", GateContext(result=result(tests_passed=False, tests_run=1))
    ).passed
    partial = GateContext(result=result(tests_passed=True, tests_run=1))
    assert not evaluate("evidence_present", partial, {"require_lint": True}).passed


def test_diff_size_max() -> None:
    ctx = GateContext(changed_files=["a"] * 10, additions=100, deletions=20)
    assert evaluate("diff_size_max", ctx, {"files": 20}).passed
    assert not evaluate("diff_size_max", ctx, {"files": 5}).passed
    assert not evaluate("diff_size_max", ctx, {"files": 20, "lines": 50}).passed


def test_no_secrets() -> None:
    assert evaluate("no_secrets", GateContext()).passed
    assert not evaluate("no_secrets", GateContext(secrets_found=["AWS key"])).passed


def test_scan_secrets_detects_common_shapes() -> None:
    assert scan_secrets("AKIA1234567890ABCDEF") == ["AWS key"]
    assert scan_secrets("-----BEGIN PRIVATE KEY-----") == ["private key"]
    assert scan_secrets("ghp_" + "a" * 30) == ["GitHub token"]
    assert scan_secrets("nothing to see") == []


def test_coverage_delta_min() -> None:
    ctx = GateContext(result=result(coverage_delta=1.2))
    assert evaluate("coverage_delta_min", ctx, {"x": 0}).passed
    assert not evaluate("coverage_delta_min", ctx, {"x": 2}).passed
    assert not evaluate("coverage_delta_min", GateContext(result=result()), {"x": 0}).passed


def test_ci_green_is_pending_then_decides() -> None:
    pending = evaluate("ci_green", GateContext())
    assert pending.pending and not pending.blocking
    assert evaluate("ci_green", GateContext(ci_status="success")).passed
    failed = evaluate("ci_green", GateContext(ci_status="failure"))
    assert failed.blocking
    # What `checks_conclusion` returns while a check runs: pending, not red (S22-14).
    running = evaluate("ci_green", GateContext(ci_status="pending"))
    assert running.pending and not running.blocking


def test_review_approved() -> None:
    assert evaluate("review_approved", GateContext()).pending
    assert evaluate("review_approved", GateContext(review_state="approved")).passed
    assert evaluate("review_approved", GateContext(review_state="changes_requested")).blocking


def test_scans_ok() -> None:
    assert evaluate("scans_ok", GateContext()).pending
    ctx = GateContext(scans={"semgrep": "ok", "trivy": "ok", "gitleaks": "ok"})
    assert evaluate("scans_ok", ctx).passed
    bad = GateContext(scans={"semgrep": "failed", "trivy": "ok", "gitleaks": "ok"})
    assert evaluate("scans_ok", bad).blocking


def test_provenance_signed() -> None:
    assert evaluate("provenance_signed", GateContext()).pending
    assert evaluate("provenance_signed", GateContext(signed=True)).passed
    assert evaluate("provenance_signed", GateContext(signed=False)).blocking


def test_without_a_provenance_check_once_the_ci_is_done_the_guarantee_fails_instead_of_waiting() -> None:
    """Nothing filled in `signed`: the guarantee waited forever (#283). With the CI done and no
    provenance check, it fails, saying what to add."""
    bare = evaluate("provenance_signed", GateContext(ci_status="success"))
    assert bare.blocking and not bare.pending
    assert "no provenance check" in bare.detail


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
    from choregos_contracts import StageResult, StageStatus

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
    from choregos_contracts import Evidence, StageResult, StageStatus

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
    from choregos_contracts import Evidence, StageResult, StageStatus

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


def test_a_guarantee_without_a_diff_does_not_rule() -> None:
    """Without a diff (SCM connector unable to compare, unreachable repository) `scope_respected`
    would declare the perimeter respected and `no_secrets` the absence of secrets, having
    looked at nothing. A guarantee that cannot be evaluated is not a guarantee."""
    blind = GateContext(diff_available=False, allowed_paths=["src/**"])
    for name in ("scope_respected", "diff_size_max", "no_secrets"):
        verdict = evaluate(name, blind, {"files": 10})
        assert not verdict.passed, name
        assert "diff unavailable" in verdict.detail

    # With a diff, nothing changes for the cases already covered.
    assert evaluate("no_secrets", GateContext()).passed


def test_tool_called_reads_the_ledger_not_the_narrative() -> None:
    """Bench of 2026-09-24: ten catalogue listings, zero calls, and `place_verified: true`
    recorded anyway. The guarantee looks at what the platform counted."""
    from choregos_contracts import StageResult, StageStatus

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


ADR = """---
status: "proposed"
---

# Use row-level security for tenant isolation

## Context and Problem Statement

Tenants share one database.

## Considered Options

* Row-level security
* One schema per tenant

## Decision Outcome

Chosen option: "Row-level security", because it keeps one schema.
"""
MADR = {
    "paths": ["docs/adr/[0-9][0-9][0-9][0-9]-*.md"],
    "sections": ["Context and Problem Statement", "Considered Options", "Decision Outcome"],
    "patterns": ['Chosen option: "'],
}


def test_markdown_sections_reads_the_added_text_not_the_narrative() -> None:
    good = GateContext(added_text={"docs/adr/0042-rls.md": ADR})
    assert evaluate("markdown_sections", good, MADR).passed
    without_decision = ADR.split("## Decision Outcome", maxsplit=1)[0]
    bad = evaluate(
        "markdown_sections", GateContext(added_text={"docs/adr/0042-rls.md": without_decision}), MADR
    )
    assert bad.blocking
    assert "docs/adr/0042-rls.md: section “Decision Outcome” missing" in bad.annotations
    assert any("not found" in a for a in bad.annotations), "the “Chosen option” pattern neither"
    # The agent's narrative does not count: only the text the branch adds.
    elsewhere = GateContext(result=_with_outputs(adr_markdown=ADR), added_text={"README.md": ADR})
    assert "0 file(s) matching" in evaluate("markdown_sections", elsewhere, MADR).detail


def test_markdown_sections_refuses_without_a_diff_and_without_a_path() -> None:
    assert "diff unavailable" in evaluate("markdown_sections", GateContext(diff_available=False), MADR).detail
    assert evaluate("markdown_sections", GateContext(added_text={"docs/adr/0042-x.md": ADR}), {}).blocking


def test_an_accepted_status_is_checked_by_a_pattern() -> None:
    accepted = {"paths": MADR["paths"], "patterns": ['(?m)^status: "?accepted"?\\s*$']}
    assert not evaluate(
        "markdown_sections", GateContext(added_text={"docs/adr/0042-rls.md": ADR}), accepted
    ).passed
    written = ADR.replace('status: "proposed"', 'status: "accepted"')
    assert evaluate(
        "markdown_sections", GateContext(added_text={"docs/adr/0042-rls.md": written}), accepted
    ).passed


# ───────────────────────── adr_number_free (#287, S22-20) ─────────────────────────

ON_MAIN = ["docs/adr/0001-queue.md", "docs/adr/0002-cache.md", "docs/adr/README.md"]


def test_adr_number_free_lets_a_free_number_through() -> None:
    free = GateContext(changed_files=["docs/adr/0003-rls.md"], default_branch_files=ON_MAIN)
    out = evaluate("adr_number_free", free)
    assert out.passed and "docs/adr/0003-rls.md" in out.detail


def test_adr_number_free_refuses_a_number_already_taken_on_the_default_branch() -> None:
    """Two studies read the same "largest plus one": the second to merge is refused."""
    taken = GateContext(changed_files=["docs/adr/0002-rls.md", "src/x.py"], default_branch_files=ON_MAIN)
    out = evaluate("adr_number_free", taken)
    assert out.blocking
    assert out.annotations == ["docs/adr/0002-rls.md"], "the files at fault"
    assert "0002 is already taken on the default branch by docs/adr/0002-cache.md" in out.detail
    assert "next free number: 0003" in out.detail


def test_adr_number_free_says_nothing_without_an_added_adr() -> None:
    without_adr = GateContext(changed_files=["src/x.py", "docs/adr/README.md"], default_branch_files=ON_MAIN)
    assert evaluate("adr_number_free", without_adr).passed
    # The same path, already on the default branch, is not an addition: it is the same file.
    touched_up = GateContext(changed_files=["docs/adr/0002-cache.md"], default_branch_files=ON_MAIN)
    assert evaluate("adr_number_free", touched_up).passed
    # Without an ADR, there is nothing to compare: the missing listing does not block.
    assert evaluate("adr_number_free", GateContext(changed_files=["src/x.py"])).passed


def test_adr_number_free_refuses_when_it_cannot_look() -> None:
    assert "diff unavailable" in evaluate("adr_number_free", GateContext(diff_available=False)).detail
    blind = evaluate("adr_number_free", GateContext(changed_files=["docs/adr/0003-rls.md"]))
    assert blind.blocking and "default branch unavailable" in blind.detail


def test_adr_number_free_follows_its_pattern() -> None:
    pattern = {"pattern": "decisions/[0-9][0-9][0-9]-*.md"}
    ctx = GateContext(changed_files=["decisions/007-x.md"], default_branch_files=["decisions/007-y.md"])
    assert evaluate("adr_number_free", ctx, pattern).blocking
    assert evaluate("adr_number_free", ctx).passed, "the default pattern does not see these files"
