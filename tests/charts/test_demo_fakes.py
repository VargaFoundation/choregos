"""The HR scenario fakes served by a single process (S20-07), and their guard.

The API runs in two replicas and the orchestrator separately: an in-memory fake would show them as many
states as there are processes. The chart can serve the fakes from ONE pod, which the API and the orchestrator
reach -- for a demonstration only: staging and prod refuse it, naming it.
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


def _rendered(*surcharges: str) -> subprocess.CompletedProcess[str]:
    command = ["helm", "template", "choregos", str(CHART)]
    for surcharge in surcharges:
        command += ["--set", surcharge]
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _documents(*surcharges: str) -> list[dict[str, Any]]:
    rendu = _rendered(*surcharges)
    assert rendu.returncode == 0, rendu.stderr
    return [doc for doc in yaml.safe_load_all(rendu.stdout) if doc]


def _nommes(documents: list[dict[str, Any]], name: str) -> dict[str, dict[str, Any]]:
    return {d["kind"]: d for d in documents if d.get("metadata", {}).get("name") == name}


def test_nothing_by_default() -> None:
    assert _nommes(_documents(), "choregos-demo-fakes") == {}


def test_one_single_pod_that_the_api_and_the_orchestrator_reach() -> None:
    documents = _documents(
        "demoFakes.enabled=true", "demoFakes.tokenSecret.name=demo", "demoFakes.tokenSecret.key=jeton"
    )
    served = _nommes(documents, "choregos-demo-fakes")
    deployment, service = served["Deployment"], served["Service"]
    assert deployment["spec"]["replicas"] == 1 and deployment["spec"]["strategy"]["type"] == "Recreate"
    (conteneur,) = deployment["spec"]["template"]["spec"]["containers"]
    assert conteneur["command"] == ["python", "-m", "choregos_adapters.fakes.server"]
    assert conteneur["image"].split("/")[-1].startswith("choregos-api:"), "the API image carries the fakes"
    token = next(e for e in conteneur["env"] if e["name"] == "CONNECTOR_DEMO_TOKEN")
    assert token["valueFrom"]["secretKeyRef"] == {"name": "demo", "key": "jeton"}, "by reference"
    assert service["spec"]["ports"][0]["port"] == 8090
    output = next(
        d
        for d in documents
        if d.get("kind") == "NetworkPolicy" and d["metadata"]["name"] == "choregos-allow-platform"
    )
    regles = [r for r in output["spec"]["egress"] if r["to"][0].get("podSelector")]
    assert regles == [
        {
            "to": [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "choregos-demo-fakes"}}}],
            "ports": [{"protocol": "TCP", "port": 8090}],
        }
    ], "the network policy opens that pod, and only that one"


@pytest.mark.parametrize("environment", ["staging", "prod"])
def test_staging_and_prod_refuse_it_by_naming_it(environment: str) -> None:
    rendu = _rendered("demoFakes.enabled=true", f"global.environment={environment}")
    assert rendu.returncode != 0 and "demoFakes.enabled" in rendu.stderr
