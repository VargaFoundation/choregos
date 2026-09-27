"""Un fait métier est-il MESURÉ, ou seulement raconté ?

`evidence_facts` (ADR 0012) a ouvert les preuves au métier : `profils_retenus: 3`,
`piece_identite: true`. Mais ces faits étaient écrits par l'agent, donc croyables et pas
vérifiables — alors que l'ADR 0010 exige qu'une garantie soit un mécanisme. Les tests, eux, ont
toujours été mesurés : le runner les exécute et écrase ce que l'agent déclare.

`dod.facts` étend ce traitement aux faits nommés : une commande par fait, code de sortie 0 = vrai.

Deux propriétés valent le détour :

  * les commandes vivent à la RACINE de la configuration, pas sous `repo` — un projet sans dépôt
    (un dossier à instruire, une plateforme à entretenir) doit pouvoir prouver quelque chose ;
  * la fusion se fait **par clé**. L'agent peut nommer cinq faits dont un seul est mesuré ;
    écraser le dictionnaire entier perdrait les quatre autres, en silence.
"""

from __future__ import annotations

import pathlib

import pytest
from choregos_contracts import Evidence, StageInput
from choregos_runner.dod import merge_evidence, run_checks

pytestmark = pytest.mark.asyncio


@pytest.fixture
def entree(stage_input: StageInput):
    """La fixture partagée, dont on ne change QUE les commandes de faits — et on éteint les
    trois autres : ce test parle des faits, pas des tests du dépôt."""

    def _avec(faits: dict[str, str]) -> StageInput:
        projet = stage_input.project.model_copy(
            update={
                "fact_commands": faits,
                "test_command": "",
                "lint_command": "",
                "typecheck_command": "",
            }
        )
        return stage_input.model_copy(update={"project": projet})

    return _avec


async def test_une_commande_qui_reussit_rend_un_fait_vrai(entree, tmp_path: pathlib.Path) -> None:
    rapport = await run_checks(entree({"constat_resolu": "true"}), tmp_path, timeout=30)
    assert rapport.facts == {"constat_resolu": True}
    assert rapport.evidence().facts == {"constat_resolu": True}


async def test_une_commande_qui_echoue_rend_faux_et_non_inconnu(entree, tmp_path: pathlib.Path) -> None:
    """Pas de « on ne sait pas » : c'est le même choix qu'une garantie sans matière, qui refuse."""
    rapport = await run_checks(entree({"constat_resolu": "false"}), tmp_path, timeout=30)
    assert rapport.facts == {"constat_resolu": False}
    assert rapport.evidence().facts == {"constat_resolu": False}


async def test_la_commande_est_vraiment_executee(entree, tmp_path: pathlib.Path) -> None:
    """Sans quoi le test ne prouverait que la plomberie, pas la mesure."""
    temoin = tmp_path / "temoin.txt"
    rapport = await run_checks(entree({"fichier_ecrit": f"touch {temoin.name}"}), tmp_path, timeout=30)
    assert temoin.exists(), "la commande n'a pas tourné dans l'espace de travail"
    assert rapport.facts == {"fichier_ecrit": True}


async def test_sans_commande_aucun_fait_n_est_mesure(entree, tmp_path: pathlib.Path) -> None:
    """Et surtout : `facts` doit rester `None`, sinon la fusion efface ceux de l'agent."""
    rapport = await run_checks(entree({}), tmp_path, timeout=30)
    assert rapport.facts == {}
    assert rapport.evidence().facts is None


async def test_une_commande_vide_est_ignoree(entree, tmp_path: pathlib.Path) -> None:
    rapport = await run_checks(entree({"jamais": "   "}), tmp_path, timeout=30)
    assert rapport.facts == {}


def test_le_fait_mesure_ecrase_celui_de_l_agent() -> None:
    declaree = Evidence(facts={"constat_resolu": True, "rapport_joint": True})
    mesuree = Evidence(facts={"constat_resolu": False})
    fusion = merge_evidence(declaree, mesuree)
    assert fusion.facts == {"constat_resolu": False, "rapport_joint": True}, (
        "la mesure doit gagner sur la déclaration, ET les faits non mesurés doivent survivre"
    )


def test_sans_fait_mesure_ceux_de_l_agent_survivent() -> None:
    declaree = Evidence(facts={"profils_retenus": 3})
    fusion = merge_evidence(declaree, Evidence(tests_passed=True))
    assert fusion.facts == {"profils_retenus": 3}
    assert fusion.tests_passed is True


@pytest.mark.parametrize("valeur", (True, False))
async def test_la_garantie_evidence_facts_juge_la_mesure(
    valeur: bool, entree, tmp_path: pathlib.Path
) -> None:
    """Le bout de la chaîne : la garantie tranche sur le fait MESURÉ, pas sur le déclaré."""
    from choregos_contracts import StageResult
    from choregos_core.gates import GateContext, evaluate

    commande = "true" if valeur else "false"
    rapport = await run_checks(entree({"constat_resolu": commande}), tmp_path, timeout=30)
    # L'agent prétend l'inverse de la mesure : c'est tout l'intérêt.
    declaree = Evidence(facts={"constat_resolu": not valeur})
    resultat = StageResult(
        schema="choregos/StageResult/v1",
        status="done",
        summary="vérification",
        evidence=merge_evidence(declaree, rapport.evidence()),
    )
    verdict = evaluate(
        "evidence_facts",
        GateContext(result=resultat),
        {"keys": ["constat_resolu"], "must_be_true": ["constat_resolu"]},
    )
    assert verdict.passed is valeur, "la garantie a suivi la déclaration, pas la mesure"
    if not valeur:
        assert verdict.blocking, "un fait mesuré faux doit BLOQUER, pas seulement ne pas passer"
