"""Every orchestrator worker receives the GitHub App, like the API (S22-04).

On 2026-10-08, the dev tenant had its GitHub App (Infisical -> `choregos-api-secrets`): the API
saw it, but the tracker catch-up failed with "[github] no authentication configured".
The chart only passed `CHOREGOS_GITHUB_APP_ID` and `CHOREGOS_GITHUB_APP_PRIVATE_KEY` to the API pod,
whereas it is the workers that read the issues and open the pull requests.
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

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _deploiements() -> list[dict[str, Any]]:
    rendu = subprocess.run(
        ["helm", "template", "choregos", str(CHART), "-f", str(CHART / "values" / "dev.yaml")],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    ).stdout
    return [d for d in yaml.safe_load_all(rendu) if d and d.get("kind") == "Deployment"]


def test_chaque_worker_de_l_orchestrateur_recoit_l_app_github_en_option() -> None:
    workers = [d for d in _deploiements() if "orchestrator" in d["metadata"]["name"]]
    assert workers, "no orchestrator worker rendered"
    for worker in workers:
        conteneur = worker["spec"]["template"]["spec"]["containers"][0]
        env = {e["name"]: e for e in conteneur.get("env", [])}
        for nom, cle in (
            ("CHOREGOS_GITHUB_APP_ID", "github-app-id"),
            ("CHOREGOS_GITHUB_APP_PRIVATE_KEY", "github-app-private-key"),
        ):
            assert nom in env, f"{worker['metadata']['name']} does not receive {nom}"
            ref = env[nom]["valueFrom"]["secretKeyRef"]
            assert (ref["name"], ref["key"], ref.get("optional")) == ("choregos-api-secrets", cle, True)
