"""Le déploiement dit ce qu'il sait faire tourner, et l'API le reçoit (#245).

`global.agents` devient trois variables de l'API : le backend et les profils de modèle dont un
projet neuf hérite. Vide, rien n'est posé — le contrat décide.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _env_de_l_api(*surcharges: str) -> dict[str, str]:
    commande = ["helm", "template", "choregos", str(CHART)]
    for surcharge in surcharges:
        commande += ["--set", surcharge]
    rendu = subprocess.run(commande, capture_output=True, text=True, check=False)
    assert rendu.returncode == 0, rendu.stderr
    documents: list[dict[str, Any]] = [d for d in yaml.safe_load_all(rendu.stdout) if d]
    deploiements = [d for d in documents if d.get("kind") == "Deployment"]
    (api,) = [d for d in deploiements if d["metadata"]["name"] == "choregos-api"]
    (conteneur,) = [c for c in api["spec"]["template"]["spec"]["containers"] if c["name"] == "api"]
    return {e["name"]: e.get("value", "") for e in conteneur.get("env", []) if "value" in e}


def test_rien_par_defaut() -> None:
    env = _env_de_l_api()
    assert not {"CHOREGOS_DEFAULT_AGENT_BACKEND", "CHOREGOS_ALLOWED_AGENT_BACKENDS"} & env.keys()
    assert "CHOREGOS_DEFAULT_MODEL_PROFILES" not in env


def test_le_backend_et_les_profils_du_deploiement_atteignent_l_api() -> None:
    env = _env_de_l_api(
        "global.agents.defaultBackend=opencode",
        "global.agents.allowedBackends={opencode}",
        "global.agents.modelProfiles.standard=platform/standard",
    )
    assert env["CHOREGOS_DEFAULT_AGENT_BACKEND"] == "opencode"
    assert json.loads(env["CHOREGOS_ALLOWED_AGENT_BACKENDS"]) == ["opencode"]
    assert json.loads(env["CHOREGOS_DEFAULT_MODEL_PROFILES"]) == {"standard": "platform/standard"}
