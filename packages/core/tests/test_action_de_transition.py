"""Une transition système propose une action gouvernée, à date (S20-05) : ce que le cœur en dit —
la date tirée d'un champ, ce que le validateur refuse, ce qui suit l'action, la phrase du processus.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from choregos_core.dsl import parse_workflow, to_process
from choregos_core.dsl.dates import DateIllisible, champ_de, echeance
from choregos_core.dsl.engine import WorkflowEngine
from choregos_core.gates import GateContext, evaluate

ARRIVEE = """
apiVersion: choregos/v1
kind: Workflow
metadata:
  name: onboarding
  version: 1
  inputs:
    type: object
    properties:
      upn: {type: string}
      date_arrivee: {type: string, format: date}
      poste: {type: string}
initial: prepare
actors:
  plateforme: {type: system}
  rh: {type: human, group: rh}
states:
  prepare: {display: Préparée, kind: wait}
  comptes: {display: Comptes créés, kind: wait}
  a_revoir: {display: À revoir, kind: wait}
  fait: {display: Fait, terminal: true}
transitions:
  - id: t-comptes
    from: prepare
    to: comptes
    by: plateforme
    action:
      kind: arrivee.comptes
      title: "Les comptes de {{ fields.upn }}"
      params: {upn: "{{ fields.upn }}"}
      effects:
        - effect: connector.call
          with: {connector: entra, operation: create_user, arguments: {upn: "{{ params.upn }}"}}
      not_before: fields.date_arrivee - 10d
    on_fail: {to: prepare, max_attempts: 2, escalate_to: a_revoir}
    on_reject: a_revoir
  - {from: comptes, to: fait, by: rh}
"""


def _arrivee(**remplacements: str) -> tuple[Any, Any]:
    texte = ARRIVEE
    for ancien, nouveau in remplacements.items():
        texte = texte.replace(ancien, nouveau)
    return parse_workflow(texte, strict=False)


def _codes(rapport: Any) -> list[str]:
    return [e.code for e in rapport.errors]


@pytest.mark.parametrize(
    ("expression", "champs", "attendu"),
    [
        ("fields.date_arrivee - 10d", {"date_arrivee": "2026-11-12"}, datetime(2026, 11, 2, tzinfo=UTC)),
        ("fields.date_arrivee", {"date_arrivee": "2026-11-12"}, datetime(2026, 11, 12, tzinfo=UTC)),
        (
            "fields.depart + 2h",
            {"depart": "2026-11-12T17:30:00+01:00"},
            datetime(2026, 11, 12, 18, 30, tzinfo=UTC),
        ),
        (
            "fields.depart - 90m",
            {"depart": "2026-11-12T10:00:00Z"},
            datetime(2026, 11, 12, 8, 30, tzinfo=UTC),
        ),
        ("fields.depart", {"depart": "2026-11-12T10:00:00"}, datetime(2026, 11, 12, 10, tzinfo=UTC)),
    ],
)
def test_une_date_se_tire_d_un_champ_decalee_et_en_utc(
    expression: str, champs: dict[str, Any], attendu: datetime
) -> None:
    assert echeance(expression, champs) == attendu


def test_sans_champ_pas_de_date_et_une_date_illisible_se_dit() -> None:
    assert echeance("fields.date_arrivee - 10d", {}) is None, "on attend le champ, on ne part pas sans date"
    assert echeance("fields.date_arrivee - 10d", {"date_arrivee": ""}) is None
    with pytest.raises(DateIllisible, match="date_arrivee"):
        echeance("fields.date_arrivee", {"date_arrivee": "lundi prochain"})
    with pytest.raises(ValueError, match="illisible"):
        champ_de("demain - 10d")
    assert champ_de("fields.date_arrivee - 10d") == "date_arrivee"


def test_un_workflow_a_action_bien_forme_est_valide() -> None:
    _, rapport = _arrivee()
    assert rapport.valid, rapport.as_dict()


SANS_DATE = "date_arrivee: {type: string}"
REPRISE = "    on_fail: {to: prepare, max_attempts: 2, escalate_to: a_revoir}\n"
VALIDATION = "  - {from: comptes, to: fait, by: rh}"
VALIDATION_GARANTIE = "  - {from: comptes, to: fait, by: rh, gates: [action_succeeded]}"


@pytest.mark.parametrize(
    ("ancien", "nouveau", "code"),
    [
        ("    by: plateforme\n    action:", "    by: rh\n    action:", "action.not_system"),
        ("not_before: fields.date_arrivee", "not_before: fields.date_depart", "action.date_field_unknown"),
        ("date_arrivee: {type: string, format: date}", SANS_DATE, "action.date_field_not_date"),
        (REPRISE, "", "action.on_fail_missing"),
        (VALIDATION, VALIDATION_GARANTIE, "gate.sans_matiere"),
    ],
)
def test_le_validateur_refuse_ce_qu_une_action_ne_peut_pas_tenir(
    ancien: str, nouveau: str, code: str
) -> None:
    _, rapport = _arrivee(**{ancien: nouveau})
    assert code in _codes(rapport), rapport.as_dict()


def test_une_action_rejetee_suit_on_reject_une_action_echouee_on_fail_une_reussie_avance() -> None:
    workflow, _ = _arrivee()
    moteur = WorkflowEngine(workflow)
    transition = workflow.transitions[0]
    assert moteur.after_action(transition, "succeeded", [], 1).next_state == "comptes"
    assert moteur.after_action(transition, "rejected", [], 1).next_state == "a_revoir"
    reprise = moteur.after_action(transition, "failed", [], 1)
    assert (reprise.next_state, reprise.retried) == ("prepare", True)
    assert moteur.after_action(transition, "failed", [], 2).next_state == "a_revoir", (
        "deux tentatives, puis l'escalade"
    )
    sans_rejet = transition.model_copy(update={"on_reject": None})
    assert moteur.after_action(sans_rejet, "rejected", [], 2).next_state == "a_revoir", (
        "un rejet est un échec"
    )


@pytest.mark.parametrize(
    ("statut", "passe", "attend"),
    [(None, False, True), ("pending_approval", False, True), ("running", False, True),
     ("succeeded", True, False), ("failed", False, False), ("rejected", False, False)],
)  # fmt: skip
def test_action_succeeded_attend_puis_tranche(statut: str | None, passe: bool, attend: bool) -> None:
    verdict = evaluate("action_succeeded", GateContext(action_status=statut))
    assert (verdict.passed, verdict.pending) == (passe, attend)


def test_la_vue_processus_dit_l_action_sa_date_et_sa_garantie_implicite() -> None:
    workflow, _ = _arrivee()
    etape = to_process(workflow)[0]
    assert [g["name"] for g in etape["gates"]] == ["action_succeeded"]
    assert "`arrivee.comptes` not before `fields.date_arrivee - 10d`" in etape["sentence"]
    assert "approved by a person when one of its operations requires it" in etape["action"]
    assert "its governed action succeeded" in etape["sentence"]
