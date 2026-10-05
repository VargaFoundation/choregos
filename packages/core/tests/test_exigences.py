"""Ce que les workflows d'un projet exigent de ses connecteurs (ADR 0034, S19-01).

La revue produit : les réglages d'un projet RH montraient `scm`, `ci`, `cd`. Un projet n'a à
brancher que ce que ses workflows lisent ; chaque exigence dit pourquoi.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from choregos_core import parse_workflow
from choregos_core.dsl import TEMPLATE_NAMES, template_yaml
from choregos_core.dsl.exigences import exigences
from choregos_core.gates import gate_needs, known_gates

STAFFING = Path(__file__).resolve().parents[3] / "demo/workflows/staffing.yaml"

ARRIVEE = """apiVersion: choregos/v1
kind: Workflow
metadata: { name: arrivee, version: 1 }
actors:
  coordinateur: { type: agent, role: plan, model: "profile:standard" }
  rh: { type: human, group: rh, sla_hours: 24 }
states:
  demande: { display: Demande, kind: wait }
  plan: { display: Plan d'accès }
  valide: { display: Validé, terminal: true }
transitions:
  - { id: t-plan, from: demande, to: plan, by: coordinateur, outputs: [plan], gates: [outputs_present] }
  - { id: t-valider, from: plan, to: valide, by: rh }
"""


def _capacites(*textes: str) -> list[str]:
    return [e.capacite for e in exigences(parse_workflow(t)[0] for t in textes)]


@pytest.mark.parametrize("nom", TEMPLATE_NAMES)
def test_un_gabarit_logiciel_exige_depot_ci_et_train(nom: str) -> None:
    assert {"scm", "ci", "cd"} <= set(_capacites(template_yaml(nom)))


def test_un_processus_rh_n_exige_ni_depot_ni_ci_ni_train() -> None:
    assert _capacites(ARRIVEE) == ["tracker", "runtime", "gateway"]
    assert _capacites(STAFFING.read_text(encoding="utf-8")) == ["tracker", "runtime", "gateway"]


def test_chaque_exigence_dit_pourquoi_et_d_ou() -> None:
    (scm,) = [
        e for e in exigences([parse_workflow(template_yaml("default-simple"))[0]]) if e.capacite == "scm"
    ]
    assert any("default-simple: the guarantee scope_respected" in raison for raison in scm.raisons)
    assert len(set(scm.raisons)) == len(scm.raisons), "sans doublon"


def test_une_garantie_qui_lit_la_ci_l_exige_meme_dans_un_processus_rh() -> None:
    avec_ci = ARRIVEE.replace("gates: [outputs_present]", "gates: [outputs_present, ci_green]")
    assert "ci" in _capacites(avec_ci)


def test_chaque_garantie_du_coeur_sait_ce_qu_elle_lit() -> None:
    """Une garantie qui lit un diff ou une CI sans le déclarer laisserait un projet sans le
    connecteur dont elle a besoin — et la garantie échouerait au premier run."""
    lisent = {g: gate_needs(g) for g in known_gates()}
    assert lisent["scope_respected"] == {"scm"}
    assert lisent["ci_green"] == {"ci"}
    assert lisent["outputs_present"] == frozenset()
