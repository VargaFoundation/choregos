"""Le chart donne-t-il à l'orchestrateur de quoi servir le catalogue d'outils ?

Le sidecar MCP (`choregos-tools`, `localhost:7777`) est ce qui permet à un agent d'appeler un outil
du catalogue. Il n'existait que dans la Task **Tekton** ; sous l'exécuteur `k8s_job`, aucun serveur
n'était monté, et c'est la raison pour laquelle le registre de coûts n'a jamais porté de ligne
`kind=tool`. L'orchestrateur monte désormais ce sidecar lui-même — à condition que le chart lui
dise quelle image employer.

La propriété qui compte est la conditionnelle : **pas de catalogue, pas de sidecar**. Un
déploiement qui n'a rien demandé ne reçoit pas un conteneur de plus dans chaque pod d'agent.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest
import yaml

RACINE = pathlib.Path(__file__).resolve().parents[2]
CHART = RACINE / "charts" / "choregos"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _env_de_l_orchestrateur(*surcharges: str) -> dict[str, str]:
    commande = ["helm", "template", "choregos", str(CHART)]
    for surcharge in surcharges:
        commande += ["--set", surcharge]
    rendu = subprocess.run(commande, capture_output=True, text=True, check=True).stdout
    for doc in yaml.safe_load_all(rendu):
        if not doc or doc.get("kind") != "Deployment":
            continue
        if "orchestrator-orchestrator" not in doc["metadata"]["name"]:
            continue
        conteneur = doc["spec"]["template"]["spec"]["containers"][0]
        return {v["name"]: v.get("value", "") for v in conteneur.get("env", [])}
    raise AssertionError("aucun déploiement d'orchestrateur rendu")


def test_sans_catalogue_aucune_image_d_outils_n_est_passee() -> None:
    """Le défaut ne change pas : rien de plus dans les pods d'agent."""
    assert "CHOREGOS_TOOLS_IMAGE" not in _env_de_l_orchestrateur()


def test_avec_un_catalogue_l_orchestrateur_sait_quelle_image_monter() -> None:
    env = _env_de_l_orchestrateur("global.toolCatalog.configMap=mon-catalogue")
    image = env.get("CHOREGOS_TOOLS_IMAGE", "")
    assert image, "l'orchestrateur ne peut pas monter un sidecar dont il ignore l'image"
    assert "choregos-tools" in image
    # Le tag suit celui du déploiement : figé, il désignerait une image absente, et le pod
    # d'agent partirait en ErrImagePull sans que rien d'autre l'explique.
    assert image.endswith(f":{_tag_du_chart()}"), image


def test_le_tag_du_sidecar_reste_surchargeable() -> None:
    env = _env_de_l_orchestrateur(
        "global.toolCatalog.configMap=mon-catalogue",
        "choregos-orchestrator.runner.toolsTag=1.2.3",
    )
    assert env["CHOREGOS_TOOLS_IMAGE"].endswith(":1.2.3")


def _tag_du_chart() -> str:
    valeurs = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    return str(valeurs["global"]["imageTag"])
