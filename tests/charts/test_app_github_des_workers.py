"""Chaque worker de l'orchestrateur reçoit l'App GitHub, comme l'API (S22-04).

Le 2026-10-08, le locataire dev avait son App GitHub (Infisical → `choregos-api-secrets`) : l'API la
voyait, mais le rattrapage du tracker échouait en « [github] aucune authentification configurée ».
Le chart ne passait `CHOREGOS_GITHUB_APP_ID` et `CHOREGOS_GITHUB_APP_PRIVATE_KEY` qu'au pod de l'API,
alors que ce sont les workers qui lisent les issues et ouvrent les pull requests.
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
    assert workers, "aucun worker de l'orchestrateur rendu"
    for worker in workers:
        conteneur = worker["spec"]["template"]["spec"]["containers"][0]
        env = {e["name"]: e for e in conteneur.get("env", [])}
        for nom, cle in (
            ("CHOREGOS_GITHUB_APP_ID", "github-app-id"),
            ("CHOREGOS_GITHUB_APP_PRIVATE_KEY", "github-app-private-key"),
        ):
            assert nom in env, f"{worker['metadata']['name']} ne reçoit pas {nom}"
            ref = env[nom]["valueFrom"]["secretKeyRef"]
            assert (ref["name"], ref["key"], ref.get("optional")) == ("choregos-api-secrets", cle, True)
