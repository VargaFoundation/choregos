"""Chaque gate a un test vert et un test rouge (S1-05)."""

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
    with pytest.raises(GateError, match="gate inconnue"):
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
    assert not evaluate("no_secrets", GateContext(secrets_found=["clé AWS"])).passed


def test_scan_secrets_detects_common_shapes() -> None:
    assert scan_secrets("AKIA1234567890ABCDEF") == ["clé AWS"]
    assert scan_secrets("-----BEGIN PRIVATE KEY-----") == ["clé privée"]
    assert scan_secrets("ghp_" + "a" * 30) == ["token GitHub"]
    assert scan_secrets("rien à voir") == []


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


def test_flag_present() -> None:
    assert evaluate("flag_present", GateContext(flags=["new-billing"]), {"name": "new-billing"}).passed
    out = evaluate("flag_present", GateContext(flags=[]), {"name": "new-billing"})
    assert out.blocking and "absent" in out.detail
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


def test_outputs_present_est_la_gate_des_metiers_sans_tests() -> None:
    """Un workflow hors logiciel n'a ni tests ni diff : sa garantie mécanique est que
    l'étape a bien produit ce que la transition déclare."""
    from choregos_contracts import StageResult, StageStatus

    done = StageResult(
        schema="choregos/StageResult/v1",
        status=StageStatus.DONE,
        summary="trois candidats",
        outputs={"shortlist": "3 profils", "notes": "entretiens planifiés"},
    )
    ctx = GateContext(result=done, expected_outputs=["shortlist", "notes"])
    assert evaluate("outputs_present", ctx).passed

    manquant = GateContext(result=done, expected_outputs=["shortlist", "rapport"])
    verdict = evaluate("outputs_present", manquant)
    assert not verdict.passed
    assert "rapport" in verdict.detail

    assert evaluate("outputs_present", GateContext()).passed, "aucune sortie déclarée : rien à exiger"
    assert not evaluate("outputs_present", GateContext(expected_outputs=["x"])).passed


def test_une_garantie_sans_diff_ne_se_prononce_pas() -> None:
    """Sans diff — connecteur SCM incapable de comparer, dépôt injoignable — `scope_respected`
    déclarerait le périmètre respecté et `no_secrets` l'absence de secrets, faute d'avoir
    regardé quoi que ce soit. Une garantie qu'on ne peut pas évaluer n'est pas une garantie."""
    aveugle = GateContext(diff_available=False, allowed_paths=["src/**"])
    for nom in ("scope_respected", "diff_size_max", "no_secrets"):
        verdict = evaluate(nom, aveugle, {"files": 10})
        assert not verdict.passed, nom
        assert "diff indisponible" in verdict.detail

    # Avec un diff, rien ne change pour les cas déjà couverts.
    assert evaluate("no_secrets", GateContext()).passed
