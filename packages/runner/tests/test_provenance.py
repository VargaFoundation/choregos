"""Ce que la plateforme a mesuré, et ce que l'agent a déclaré (ADR 0045, S25-03).

Les preuves mesurées écrasent les déclarées (`merge_evidence`) — mais le résultat fusionné ne disait
plus lesquelles l'étaient : la console affichait « tests : ✓ 412 » de la même façon, que le runner
les ait exécutés ou que l'agent l'ait écrit. `Evidence.measured` le dit, champ par champ.
"""

from __future__ import annotations

from choregos_contracts import Evidence
from choregos_runner.dod import merge_evidence


def test_la_fusion_dit_ce_qui_est_mesure_champ_par_champ() -> None:
    declaree = Evidence(
        tests_passed=True,
        tests_run=12,
        coverage_delta=1.5,
        facts={"rapport_joint": True, "constat_resolu": True},
    )
    mesuree = Evidence(tests_passed=False, tests_run=3, lint="ok", facts={"constat_resolu": False})
    fusion = merge_evidence(declaree, mesuree)
    assert fusion.measured == ["facts.constat_resolu", "lint", "tests_passed", "tests_run"]
    # La couverture et le rapport joint viennent du récit de l'agent : ils restent, non mesurés.
    assert fusion.coverage_delta == 1.5
    assert fusion.facts == {"rapport_joint": True, "constat_resolu": False}


def test_un_agent_ne_peut_pas_se_dire_mesure() -> None:
    declaree = Evidence(tests_passed=True, tests_run=40, measured=["tests_passed", "tests_run"])
    assert merge_evidence(declaree, Evidence()).measured == []


def test_rien_de_mesure_tout_est_declare() -> None:
    """Une liste vide, pas `None` : `None` voudrait dire « un runner trop ancien pour le dire »."""
    assert merge_evidence(Evidence(tests_run=3), Evidence()).measured == []
