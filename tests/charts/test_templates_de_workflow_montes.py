"""Le chart sait-il apporter des templates de workflow au déploiement ?

`CHOREGOS_WORKFLOW_TEMPLATES_DIR` a été ouvert dans le cœur le 2026-09-26. Le chart ne savait
pas encore le poser : la couture existait donc côté code et **pas côté déploiement**, ce qui est
la pire moitié — un réglage qu'on ne peut pas régler là où on déploie ne sert à personne.

Deux propriétés, et la seconde est celle qui a failli manquer : **l'API autant que
l'orchestrateur**. L'orchestrateur exécute les étapes, mais c'est l'API qui charge la définition
d'un workflow et sert le catalogue de templates. Un montage sur l'un seul donnerait deux
processus qui ne connaissent pas les mêmes templates — et le désaccord se verrait au moment de
créer un projet, loin d'ici.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

RACINE = pathlib.Path(__file__).resolve().parents[2]
CHART = RACINE / "charts" / "choregos"
CONFIGMAP = "mes-templates"
VARIABLE = "CHOREGOS_WORKFLOW_TEMPLATES_DIR"
#: Les déploiements qui doivent connaître les templates, et pourquoi.
ATTENDUS = {
    "choregos-api": "charge la définition d'un workflow et sert le catalogue",
    "choregos-orchestrator-orchestrator": "exécute les étapes du workflow",
}

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _rendu(*surcharges: str) -> list[dict[str, Any]]:
    commande = ["helm", "template", "choregos", str(CHART)]
    for surcharge in surcharges:
        commande += ["--set", surcharge]
    sortie = subprocess.run(commande, capture_output=True, text=True, check=True, timeout=120)
    return [doc for doc in yaml.safe_load_all(sortie.stdout) if doc]


def _deploiements(docs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        doc["metadata"]["name"]: doc["spec"]["template"]["spec"]
        for doc in docs
        if doc.get("kind") == "Deployment"
    }


def test_sans_configmap_rien_n_est_monte() -> None:
    """Le défaut reste les trois templates du paquet, sans volume ni variable."""
    for nom, spec in _deploiements(_rendu()).items():
        volumes = {v["name"] for v in spec.get("volumes", [])}
        assert "workflow-templates" not in volumes, f"{nom} monte un volume que personne n'a demandé"
        for conteneur in spec.get("containers", []):
            noms = {v["name"] for v in conteneur.get("env", []) or []}
            assert VARIABLE not in noms, f"{nom} pose {VARIABLE} sans ConfigMap"


def test_les_playbooks_aussi_sont_vus_des_deux_cotes() -> None:
    """Le même défaut existait pour les playbooks, et je l'ai écrit juste après l'avoir décrit.

    Ils n'étaient montés que sur l'orchestrateur. Or l'API **valide** un workflow : sans les
    playbooks elle avertit `role.playbook_introuvable` sur des rôles que le déploiement fournit
    bel et bien, et un avertissement faux apprend à ignorer les avertissements.
    """
    docs = _rendu("global.playbooks.configMap=mes-roles")
    porteurs = {
        nom
        for nom, spec in _deploiements(docs).items()
        for conteneur in spec.get("containers", [])
        if any(m["name"] == "playbooks" for m in conteneur.get("volumeMounts", []) or [])
    }
    assert "choregos-api" in porteurs, "l'API ne voit pas les playbooks : elle valide à l'aveugle"
    assert "choregos-orchestrator-orchestrator" in porteurs, "l'orchestrateur ne les voit pas"


@pytest.mark.parametrize("deploiement", sorted(ATTENDUS))
def test_l_api_et_l_orchestrateur_voient_les_memes_templates(deploiement: str) -> None:
    """Un montage sur un seul des deux, et les deux processus ne connaîtraient pas les mêmes
    templates — un désaccord qui se verrait à la création d'un projet, loin de la cause."""
    docs = _rendu(f"global.workflowTemplates.configMap={CONFIGMAP}")
    deploiements = _deploiements(docs)
    assert deploiement in deploiements, f"{deploiement} n'est pas rendu ({sorted(deploiements)})"
    spec = deploiements[deploiement]

    source = [v for v in spec.get("volumes", []) if (v.get("configMap") or {}).get("name") == CONFIGMAP]
    assert source, f"{deploiement} : le ConfigMap n'est pas monté ({ATTENDUS[deploiement]})"

    conteneur = spec["containers"][0]
    chemins = {m["mountPath"] for m in conteneur.get("volumeMounts", []) if m["name"] == "workflow-templates"}
    assert chemins, f"{deploiement} : volume déclaré mais pas monté dans le conteneur"

    variables = {v["name"]: v.get("value") for v in conteneur.get("env", []) or []}
    assert VARIABLE in variables, f"{deploiement} : {VARIABLE} absente — le code ne cherchera pas"
    assert variables[VARIABLE] in chemins, (
        f"{deploiement} : {VARIABLE} vaut {variables[VARIABLE]!r} et le volume est monté sur "
        f"{sorted(chemins)} — le processus lira un dossier vide"
    )
