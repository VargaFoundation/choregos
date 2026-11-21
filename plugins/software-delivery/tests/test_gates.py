"""Each gate of software delivery has a green test and a red test (S1-05), on the facts of the plugin's
`delivery` provider (ADR 0181, round C2): they were the core's until then."""

from __future__ import annotations

import pytest
from tranibor_contracts import Evidence, StageResult, StageStatus
from tranibor_core import evaluate, is_async_gate, known_gates
from tranibor_software_delivery.gates import DeliveryFacts, delivery_context


def result(**evidence: object) -> StageResult:
    return StageResult(status=StageStatus.DONE, summary="ok", evidence=Evidence(**evidence))  # type: ignore[arg-type]


def _with_outputs(**outputs: object) -> StageResult:
    return StageResult(status=StageStatus.DONE, summary="ok", outputs=outputs)  # type: ignore[arg-type]


def test_the_twelve_are_registered() -> None:
    assert {"ci_green", "scans_ok", "scope_respected", "review_approved", "coverage_delta_min"} <= set(
        known_gates()
    )
    assert is_async_gate("ci_green")
    assert not is_async_gate("scope_respected")


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
    out = evaluate(
        "scope_respected", delivery_context(DeliveryFacts(changed_files=changed, allowed_paths=allowed))
    )
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
        "scope_respected",
        delivery_context(DeliveryFacts(changed_files=changed, allowed_paths=allowed, denied_paths=denied)),
    )
    assert out.passed is expected, out.detail
    if not expected:
        assert "scope.deny_paths" in out.detail
        assert list(out.annotations) == [
            p for p in changed if p.startswith((".github/workflows/", "src/generated/"))
        ]


def test_diff_size_max() -> None:
    ctx = delivery_context(DeliveryFacts(changed_files=["a"] * 10, additions=100, deletions=20))
    assert evaluate("diff_size_max", ctx, {"files": 20}).passed
    assert not evaluate("diff_size_max", ctx, {"files": 5}).passed
    assert not evaluate("diff_size_max", ctx, {"files": 20, "lines": 50}).passed


def test_no_secrets() -> None:
    assert evaluate("no_secrets", delivery_context()).passed
    assert not evaluate("no_secrets", delivery_context(DeliveryFacts(secrets_found=["AWS key"]))).passed


def test_coverage_delta_min() -> None:
    ctx = delivery_context(result=result(coverage_delta=1.2))
    assert evaluate("coverage_delta_min", ctx, {"x": 0}).passed
    assert not evaluate("coverage_delta_min", ctx, {"x": 2}).passed
    assert not evaluate("coverage_delta_min", delivery_context(result=result()), {"x": 0}).passed


def test_ci_green_is_pending_then_decides() -> None:
    pending = evaluate("ci_green", delivery_context())
    assert pending.pending and not pending.blocking
    assert evaluate("ci_green", delivery_context(DeliveryFacts(ci_status="success"))).passed
    failed = evaluate("ci_green", delivery_context(DeliveryFacts(ci_status="failure")))
    assert failed.blocking
    # What `checks_conclusion` returns while a check runs: pending, not red (S22-14).
    running = evaluate("ci_green", delivery_context(DeliveryFacts(ci_status="pending")))
    assert running.pending and not running.blocking


def test_review_approved() -> None:
    assert evaluate("review_approved", delivery_context()).pending
    assert evaluate("review_approved", delivery_context(DeliveryFacts(review_state="approved"))).passed
    assert evaluate(
        "review_approved", delivery_context(DeliveryFacts(review_state="changes_requested"))
    ).blocking


def test_scans_ok() -> None:
    assert evaluate("scans_ok", delivery_context()).pending
    ctx = delivery_context(DeliveryFacts(scans={"semgrep": "ok", "trivy": "ok", "gitleaks": "ok"}))
    assert evaluate("scans_ok", ctx).passed
    bad = delivery_context(DeliveryFacts(scans={"semgrep": "failed", "trivy": "ok", "gitleaks": "ok"}))
    assert evaluate("scans_ok", bad).blocking


def test_provenance_signed() -> None:
    assert evaluate("provenance_signed", delivery_context()).pending
    assert evaluate("provenance_signed", delivery_context(DeliveryFacts(signed=True))).passed
    assert evaluate("provenance_signed", delivery_context(DeliveryFacts(signed=False))).blocking


def test_without_a_provenance_check_once_the_ci_is_done_the_guarantee_fails_instead_of_waiting() -> None:
    """Nothing filled in `signed`: the guarantee waited forever (#283). With the CI done and no
    provenance check, it fails, saying what to add."""
    bare = evaluate("provenance_signed", delivery_context(DeliveryFacts(ci_status="success")))
    assert bare.blocking and not bare.pending
    assert "no provenance check" in bare.detail


def test_a_guarantee_without_a_diff_does_not_rule() -> None:
    """Without a diff (SCM connector unable to compare, unreachable repository) `scope_respected`
    would declare the perimeter respected and `no_secrets` the absence of secrets, having
    looked at nothing. A guarantee that cannot be evaluated is not a guarantee."""
    blind = delivery_context(DeliveryFacts(diff_available=False, allowed_paths=["src/**"]))
    for name in ("scope_respected", "diff_size_max", "no_secrets"):
        verdict = evaluate(name, blind, {"files": 10})
        assert not verdict.passed, name
        assert "diff unavailable" in verdict.detail

    # With a diff, nothing changes for the cases already covered.
    assert evaluate("no_secrets", delivery_context()).passed


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
    good = delivery_context(DeliveryFacts(added_text={"docs/adr/0042-rls.md": ADR}))
    assert evaluate("markdown_sections", good, MADR).passed
    without_decision = ADR.split("## Decision Outcome", maxsplit=1)[0]
    bad = evaluate(
        "markdown_sections",
        delivery_context(DeliveryFacts(added_text={"docs/adr/0042-rls.md": without_decision})),
        MADR,
    )
    assert bad.blocking
    assert "docs/adr/0042-rls.md: section “Decision Outcome” missing" in bad.annotations
    assert any("not found" in a for a in bad.annotations), "the “Chosen option” pattern neither"
    # The agent's narrative does not count: only the text the branch adds.
    elsewhere = delivery_context(
        DeliveryFacts(added_text={"README.md": ADR}), result=_with_outputs(adr_markdown=ADR)
    )
    assert "0 file(s) matching" in evaluate("markdown_sections", elsewhere, MADR).detail


def test_markdown_sections_refuses_without_a_diff_and_without_a_path() -> None:
    assert (
        "diff unavailable"
        in evaluate("markdown_sections", delivery_context(DeliveryFacts(diff_available=False)), MADR).detail
    )
    assert evaluate(
        "markdown_sections", delivery_context(DeliveryFacts(added_text={"docs/adr/0042-x.md": ADR})), {}
    ).blocking


def test_an_accepted_status_is_checked_by_a_pattern() -> None:
    accepted = {"paths": MADR["paths"], "patterns": ['(?m)^status: "?accepted"?\\s*$']}
    assert not evaluate(
        "markdown_sections",
        delivery_context(DeliveryFacts(added_text={"docs/adr/0042-rls.md": ADR})),
        accepted,
    ).passed
    written = ADR.replace('status: "proposed"', 'status: "accepted"')
    assert evaluate(
        "markdown_sections",
        delivery_context(DeliveryFacts(added_text={"docs/adr/0042-rls.md": written})),
        accepted,
    ).passed


ON_MAIN = ["docs/adr/0001-queue.md", "docs/adr/0002-cache.md", "docs/adr/README.md"]


def test_adr_number_free_lets_a_free_number_through() -> None:
    free = delivery_context(
        DeliveryFacts(changed_files=["docs/adr/0003-rls.md"], default_branch_files=ON_MAIN)
    )
    out = evaluate("adr_number_free", free)
    assert out.passed and "docs/adr/0003-rls.md" in out.detail


def test_adr_number_free_refuses_a_number_already_taken_on_the_default_branch() -> None:
    """Two studies read the same "largest plus one": the second to merge is refused."""
    taken = delivery_context(
        DeliveryFacts(changed_files=["docs/adr/0002-rls.md", "src/x.py"], default_branch_files=ON_MAIN)
    )
    out = evaluate("adr_number_free", taken)
    assert out.blocking
    assert out.annotations == ["docs/adr/0002-rls.md"], "the files at fault"
    assert "0002 is already taken on the default branch by docs/adr/0002-cache.md" in out.detail
    assert "next free number: 0003" in out.detail


def test_adr_number_free_says_nothing_without_an_added_adr() -> None:
    without_adr = delivery_context(
        DeliveryFacts(changed_files=["src/x.py", "docs/adr/README.md"], default_branch_files=ON_MAIN)
    )
    assert evaluate("adr_number_free", without_adr).passed
    # The same path, already on the default branch, is not an addition: it is the same file.
    touched_up = delivery_context(
        DeliveryFacts(changed_files=["docs/adr/0002-cache.md"], default_branch_files=ON_MAIN)
    )
    assert evaluate("adr_number_free", touched_up).passed
    # Without an ADR, there is nothing to compare: the missing listing does not block.
    assert evaluate("adr_number_free", delivery_context(DeliveryFacts(changed_files=["src/x.py"]))).passed


def test_adr_number_free_refuses_when_it_cannot_look() -> None:
    assert (
        "diff unavailable"
        in evaluate("adr_number_free", delivery_context(DeliveryFacts(diff_available=False))).detail
    )
    blind = evaluate(
        "adr_number_free", delivery_context(DeliveryFacts(changed_files=["docs/adr/0003-rls.md"]))
    )
    assert blind.blocking and "default branch unavailable" in blind.detail


def test_adr_number_free_follows_its_pattern() -> None:
    pattern = {"pattern": "decisions/[0-9][0-9][0-9]-*.md"}
    ctx = delivery_context(
        DeliveryFacts(changed_files=["decisions/007-x.md"], default_branch_files=["decisions/007-y.md"])
    )
    assert evaluate("adr_number_free", ctx, pattern).blocking
    assert evaluate("adr_number_free", ctx).passed, "the default pattern does not see these files"


def test_the_gates_read_the_evidence_as_named_facts() -> None:
    """ADR 0181 C2: a result whose evidence is facts only is judged as one with the fields."""
    facts_only = StageResult(
        status=StageStatus.DONE,
        summary="ok",
        evidence=Evidence(facts={"coverage_delta": 1.5, "tests_passed": True, "tests_run": 3}),
    )
    assert evaluate("coverage_delta_min", delivery_context(result=facts_only), {"x": 1}).passed
    assert not evaluate("coverage_delta_min", delivery_context(result=facts_only), {"x": 2}).passed
    assert evaluate("evidence_present", delivery_context(result=facts_only)).passed
