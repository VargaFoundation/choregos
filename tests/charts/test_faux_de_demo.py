"""Les faux du scénario RH servis par un seul processus (S20-07), et leur garde.

L'API tourne en deux répliques et l'orchestrateur à part : un faux en mémoire leur montrerait autant
d'états que de processus. Le chart sait servir les faux depuis UN pod, que l'API et l'orchestrateur
joignent — pour une démonstration seulement : staging et prod le refusent, en le nommant.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _rendu(*surcharges: str) -> subprocess.CompletedProcess[str]:
    commande = ["helm", "template", "choregos", str(CHART)]
    for surcharge in surcharges:
        commande += ["--set", surcharge]
    return subprocess.run(commande, capture_output=True, text=True, check=False)


def _documents(*surcharges: str) -> list[dict[str, Any]]:
    rendu = _rendu(*surcharges)
    assert rendu.returncode == 0, rendu.stderr
    return [doc for doc in yaml.safe_load_all(rendu.stdout) if doc]


def _nommes(documents: list[dict[str, Any]], nom: str) -> dict[str, dict[str, Any]]:
    return {d["kind"]: d for d in documents if d.get("metadata", {}).get("name") == nom}


def test_rien_par_defaut() -> None:
    assert _nommes(_documents(), "choregos-demo-fakes") == {}


def test_un_seul_pod_que_l_api_et_l_orchestrateur_joignent() -> None:
    documents = _documents(
        "demoFakes.enabled=true", "demoFakes.tokenSecret.name=demo", "demoFakes.tokenSecret.key=jeton"
    )
    servis = _nommes(documents, "choregos-demo-fakes")
    deploiement, service = servis["Deployment"], servis["Service"]
    assert deploiement["spec"]["replicas"] == 1 and deploiement["spec"]["strategy"]["type"] == "Recreate"
    (conteneur,) = deploiement["spec"]["template"]["spec"]["containers"]
    assert conteneur["command"] == ["python", "-m", "choregos_adapters.fakes.serveur"]
    assert conteneur["image"].split("/")[-1].startswith("choregos-api:"), "l'image de l'API porte les faux"
    jeton = next(e for e in conteneur["env"] if e["name"] == "CHOREGOS_DEMO_JETON")
    assert jeton["valueFrom"]["secretKeyRef"] == {"name": "demo", "key": "jeton"}, "par référence"
    assert service["spec"]["ports"][0]["port"] == 8090
    sortie = next(
        d
        for d in documents
        if d.get("kind") == "NetworkPolicy" and d["metadata"]["name"] == "choregos-allow-platform"
    )
    regles = [r for r in sortie["spec"]["egress"] if r["to"][0].get("podSelector")]
    assert regles == [
        {
            "to": [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "choregos-demo-fakes"}}}],
            "ports": [{"protocol": "TCP", "port": 8090}],
        }
    ], "la politique réseau ouvre ce pod-là, et lui seul"


@pytest.mark.parametrize("environnement", ["staging", "prod"])
def test_staging_et_prod_le_refusent_en_le_nommant(environnement: str) -> None:
    rendu = _rendu("demoFakes.enabled=true", f"global.environment={environnement}")
    assert rendu.returncode != 0 and "demoFakes.enabled" in rendu.stderr
