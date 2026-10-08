"""Une tâche humaine (S20-06) : ce que le validateur exige — une personne, un formulaire d'objet dont
chaque propriété est un champ déclaré — et la phrase que la vue processus en fait."""

from __future__ import annotations

import pytest
from choregos_core.dsl import parse_workflow, to_process

BADGE = """
apiVersion: choregos/v1
kind: Workflow
metadata:
  name: onboarding
  version: 1
  inputs:
    type: object
    properties:
      upn: {type: string}
      badge_uid: {type: string}
initial: badge
actors:
  accueil: {type: human, group: accueil}
states:
  badge: {display: Badge à remettre, kind: wait}
  fait: {display: Fait, terminal: true}
transitions:
  - id: t-badge
    from: badge
    to: fait
    by: accueil
    task:
      title: Remettre le badge
      instructions: En main propre, puis le passer au lecteur.
      form:
        type: object
        required: [badge_uid]
        properties:
          badge_uid: {type: string, minLength: 8}
      attest: J'ai remis le badge en main propre à son porteur
"""


def _codes(texte: str) -> list[str]:
    _, rapport = parse_workflow(texte, strict=False)
    return [e.code for e in rapport.errors]


def test_une_tache_bien_formee_est_valide_et_se_dit_en_clair() -> None:
    workflow, rapport = parse_workflow(BADGE, strict=False)
    assert rapport.valid, rapport.as_dict()
    etape = to_process(workflow)[0]
    assert etape["task"] == (
        "done “Remettre le badge” (filling `badge_uid`) and attested “J'ai remis le badge en main propre à "
        "son porteur”"
    )
    assert "once they have done “Remettre le badge”" in etape["sentence"]


CHAMP = "          badge_uid: {type: string, minLength: 8}"
OBJET = "        type: object\n        required: [badge_uid]"
PROPRIETES = "        properties:\n" + CHAMP


@pytest.mark.parametrize(
    ("ancien", "nouveau", "code"),
    [
        ("  accueil: {type: human, group: accueil}", "  accueil: {type: system}", "task.not_human"),
        (CHAMP, "          numero: {type: string}", "task.field_unknown"),
        (OBJET, OBJET.replace("object", "objet"), "task.form_invalid"),
        (PROPRIETES, "        properties: {}", "task.form_invalid"),
    ],
)
def test_le_validateur_refuse_une_tache_qu_on_ne_peut_pas_faire(ancien: str, nouveau: str, code: str) -> None:
    assert ancien in BADGE
    assert code in _codes(BADGE.replace(ancien, nouveau))
