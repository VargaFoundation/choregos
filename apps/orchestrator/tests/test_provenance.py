"""La provenance se lit sur la PR (#283) : un check dont le nom contient `provenance`."""

from __future__ import annotations

from choregos_core.domain import CheckRun


def test_la_provenance_se_lit_dans_les_checks_de_la_pr() -> None:
    from choregos_orchestrator.activities.gates import _provenance

    def check(nom: str, conclusion: str | None) -> CheckRun:
        return CheckRun(name=nom, status="completed" if conclusion else "in_progress", conclusion=conclusion)

    assert _provenance([check("ci", "success")]) is None, "pas de check de provenance : on ne sait pas"
    assert _provenance([check("attest-build-provenance", None)]) is None, "pas encore terminé"
    assert _provenance([check("attest-build-provenance", "success")]) is True
    assert _provenance([check("SLSA Provenance", "failure"), check("ci", "success")]) is False
