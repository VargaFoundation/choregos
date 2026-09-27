"""Une garantie livrée HORS de cet arbre est-elle acceptée par le validateur et évaluée ?

La couture de greffons (`choregos.plugins`) permet à un paquet installé à côté d'enregistrer ses
connecteurs. Le plan supposait qu'une *garantie* demanderait quatre modifications dans le cœur —
la fonction décorée, un champ sur `GateContext`, son peuplement dans l'orchestrateur, et
`GATES_A_MATIERE` pour le refus statique.

Ce test met la supposition à l'épreuve. Ce qui compte n'est pas qu'elle soit juste ou fausse,
c'est de le savoir : tant qu'on ne l'a pas essayé, « c'est extensible » est une opinion.

La vraie question est la première : le DSL refuse une garantie inconnue par une ERREUR bloquante
(`gate.unknown`). Une garantie enregistrée après le démarrage doit donc devenir connue du
validateur, sinon un workflow qui l'utilise est invalide et rien ne s'exécute jamais.
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_contracts import Evidence, StageResult
from choregos_core import parse_workflow
from choregos_core.gates import GateContext, GateOutcome, evaluate, gate, known_gates

NOM = "collecteur_propre"

WORKFLOW = """
apiVersion: choregos/v1
kind: Workflow
metadata: { name: maison, version: 1 }
actors:
  agent_maison: { type: agent, role: implement, model: "profile:standard" }
  ci: { type: system }
states:
  inbox: { display: À trier, kind: wait }
  verification: { display: Vérification }
  fini: { display: Fini, terminal: true }
transitions:
  - id: t-faire
    from: inbox
    to: verification
    by: agent_maison
    outputs: [rapport]
    on_fail: { to: inbox, max_attempts: 2, escalate_to: fini }
  - id: t-verifier
    from: verification
    to: fini
    by: ci
    gates: [collecteur_propre]
"""


@pytest.fixture
def garantie_maison() -> Any:
    """Enregistre une garantie comme le ferait un greffon, puis la retire."""
    from choregos_core.gates import _REGISTRY

    avant = dict(_REGISTRY)

    @gate(NOM)
    def _collecteur_propre(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
        """Lit ce que l'étape a produit — rien d'autre n'est nécessaire."""
        faits = (ctx.result.evidence.facts or {}) if ctx.result else {}
        propre = bool(faits.get("collecteur_propre"))
        return GateOutcome(NOM, propre, detail="collecteur propre" if propre else "constat encore là")

    yield
    _REGISTRY.clear()
    _REGISTRY.update(avant)


def test_sans_le_greffon_le_workflow_est_invalide() -> None:
    """La preuve que le test ne se ment pas : la garantie doit vraiment manquer au départ."""
    assert NOM not in known_gates()
    _, rapport = parse_workflow(WORKFLOW, strict=False)
    codes = [erreur.code for erreur in rapport.errors]
    assert "gate.unknown" in codes, f"attendu un refus de garantie inconnue, obtenu {codes}"


def test_avec_le_greffon_le_validateur_l_accepte(garantie_maison: None) -> None:
    """C'est la propriété qui décide de tout : sans elle, le workflow ne démarre jamais."""
    assert NOM in known_gates()
    _, rapport = parse_workflow(WORKFLOW, strict=False)
    assert rapport.valid, [f"{e.code}: {e.message}" for e in rapport.errors]


def test_et_elle_est_evaluee_sur_le_resultat_de_l_etape(garantie_maison: None) -> None:
    """Aucun champ ajouté à `GateContext` : la garantie lit `result`, qui porte déjà les faits."""
    for valeur, attendu in ((True, True), (False, False)):
        resultat = StageResult(
            schema="choregos/StageResult/v1",
            status="done",
            summary="essai",
            evidence=Evidence(facts={"collecteur_propre": valeur}),
        )
        verdict = evaluate(NOM, GateContext(result=resultat))
        assert verdict.passed is attendu
        assert verdict.blocking is (not attendu), "une garantie qui ne passe pas doit bloquer"
