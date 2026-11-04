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

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART = ROOT / "charts" / "choregos"
CONFIGMAP = "mes-templates"
VARIABLE = "CHOREGOS_WORKFLOW_TEMPLATES_DIR"
#: The deployments that must know the templates, and why.
EXPECTED = {
    "choregos-api": "loads a workflow's definition and serves the catalogue",
    "choregos-orchestrator-orchestrator": "executes the workflow's steps",
}

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _rendered(*surcharges: str) -> list[dict[str, Any]]:
    command = ["helm", "template", "choregos", str(CHART)]
    for surcharge in surcharges:
        command += ["--set", surcharge]
    output = subprocess.run(command, capture_output=True, text=True, check=True, timeout=120)
    return [doc for doc in yaml.safe_load_all(output.stdout) if doc]


def _deploiements(docs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        doc["metadata"]["name"]: doc["spec"]["template"]["spec"]
        for doc in docs
        if doc.get("kind") == "Deployment"
    }


def test_without_a_configmap_nothing_is_mounted() -> None:
    """The default remains the package's three templates, with no volume or variable."""
    for name, spec in _deploiements(_rendered()).items():
        volumes = {v["name"] for v in spec.get("volumes", [])}
        assert "workflow-templates" not in volumes, f"{name} mounts a volume nobody asked for"
        for conteneur in spec.get("containers", []):
            names = {v["name"] for v in conteneur.get("env", []) or []}
            assert VARIABLE not in names, f"{name} pose {VARIABLE} sans ConfigMap"


def test_the_playbooks_are_seen_on_both_sides_too() -> None:
    """The same defect existed for the playbooks, and I wrote it right after describing it.

    They were only mounted on the orchestrator. Yet the API **validates** a workflow: without the
    playbooks it warns `role.playbook_not_found` about roles the deployment does
    provide, and a false warning teaches people to ignore warnings.
    """
    docs = _rendered("global.playbooks.configMap=mes-roles")
    porteurs = {
        name
        for name, spec in _deploiements(docs).items()
        for conteneur in spec.get("containers", [])
        if any(m["name"] == "playbooks" for m in conteneur.get("volumeMounts", []) or [])
    }
    assert "choregos-api" in porteurs, "the API does not see the playbooks: it validates blind"
    assert "choregos-orchestrator-orchestrator" in porteurs, "the orchestrator does not see them"


@pytest.mark.parametrize("deployment", sorted(EXPECTED))
def test_the_api_and_the_orchestrator_see_the_same_templates(deployment: str) -> None:
    """A mount on only one of the two, and the two processes would not know the same
    templates -- a disagreement that would show up at project creation, far from the cause."""
    docs = _rendered(f"global.workflowTemplates.configMap={CONFIGMAP}")
    deploiements = _deploiements(docs)
    assert deployment in deploiements, f"{deployment} is not rendered ({sorted(deploiements)})"
    spec = deploiements[deployment]

    source = [v for v in spec.get("volumes", []) if (v.get("configMap") or {}).get("name") == CONFIGMAP]
    assert source, f"{deployment}: the ConfigMap is not mounted ({EXPECTED[deployment]})"

    conteneur = spec["containers"][0]
    paths = {m["mountPath"] for m in conteneur.get("volumeMounts", []) if m["name"] == "workflow-templates"}
    assert paths, f"{deployment}: volume declared but not mounted in the container"

    variables = {v["name"]: v.get("value") for v in conteneur.get("env", []) or []}
    assert VARIABLE in variables, f"{deployment}: {VARIABLE} absent -- the code will not look"
    assert variables[VARIABLE] in paths, (
        f"{deployment}: {VARIABLE} is {variables[VARIABLE]!r} and the volume is mounted on "
        f"{sorted(paths)} -- the process will read an empty folder"
    )
