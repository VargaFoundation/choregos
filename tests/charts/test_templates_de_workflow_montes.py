"""Does the chart know how to bring workflow templates to the deployment?

`CHOREGOS_WORKFLOW_TEMPLATES_DIR` was opened in the core on 2026-09-26. The chart did not yet
know how to set it: the seam therefore existed on the code side and **not on the deployment side**, which is
the worst half -- a setting that cannot be set where one deploys is of no use to anyone.

Two properties, and the second is the one that nearly got missed: **the API as much as
the orchestrator**. The orchestrator executes the steps, but it is the API that loads a workflow's definition
and serves the template catalogue. A mount on only one would give two
processes that do not know the same templates -- and the disagreement would show up when
creating a project, far from here.
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
#: The deployments that must know the templates, and why.
ATTENDUS = {
    "choregos-api": "loads a workflow's definition and serves the catalogue",
    "choregos-orchestrator-orchestrator": "executes the workflow's steps",
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
    """The default remains the package's three templates, with no volume or variable."""
    for nom, spec in _deploiements(_rendu()).items():
        volumes = {v["name"] for v in spec.get("volumes", [])}
        assert "workflow-templates" not in volumes, f"{nom} mounts a volume nobody asked for"
        for conteneur in spec.get("containers", []):
            noms = {v["name"] for v in conteneur.get("env", []) or []}
            assert VARIABLE not in noms, f"{nom} pose {VARIABLE} sans ConfigMap"


def test_les_playbooks_aussi_sont_vus_des_deux_cotes() -> None:
    """The same defect existed for the playbooks, and I wrote it right after describing it.

    They were only mounted on the orchestrator. Yet the API **validates** a workflow: without the
    playbooks it warns `role.playbook_not_found` about roles the deployment does
    provide, and a false warning teaches people to ignore warnings.
    """
    docs = _rendu("global.playbooks.configMap=mes-roles")
    porteurs = {
        nom
        for nom, spec in _deploiements(docs).items()
        for conteneur in spec.get("containers", [])
        if any(m["name"] == "playbooks" for m in conteneur.get("volumeMounts", []) or [])
    }
    assert "choregos-api" in porteurs, "the API does not see the playbooks: it validates blind"
    assert "choregos-orchestrator-orchestrator" in porteurs, "the orchestrator does not see them"


@pytest.mark.parametrize("deploiement", sorted(ATTENDUS))
def test_l_api_et_l_orchestrateur_voient_les_memes_templates(deploiement: str) -> None:
    """A mount on only one of the two, and the two processes would not know the same
    templates -- a disagreement that would show up at project creation, far from the cause."""
    docs = _rendu(f"global.workflowTemplates.configMap={CONFIGMAP}")
    deploiements = _deploiements(docs)
    assert deploiement in deploiements, f"{deploiement} is not rendered ({sorted(deploiements)})"
    spec = deploiements[deploiement]

    source = [v for v in spec.get("volumes", []) if (v.get("configMap") or {}).get("name") == CONFIGMAP]
    assert source, f"{deploiement}: the ConfigMap is not mounted ({ATTENDUS[deploiement]})"

    conteneur = spec["containers"][0]
    chemins = {m["mountPath"] for m in conteneur.get("volumeMounts", []) if m["name"] == "workflow-templates"}
    assert chemins, f"{deploiement}: volume declared but not mounted in the container"

    variables = {v["name"]: v.get("value") for v in conteneur.get("env", []) or []}
    assert VARIABLE in variables, f"{deploiement}: {VARIABLE} absent -- the code will not look"
    assert variables[VARIABLE] in chemins, (
        f"{deploiement}: {VARIABLE} is {variables[VARIABLE]!r} and the volume is mounted on "
        f"{sorted(chemins)} -- the process will read an empty folder"
    )
