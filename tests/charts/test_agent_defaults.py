"""The deployment says what it knows how to run, and the API receives it (#245).

`global.agents` becomes three API variables: the backend and the model profiles a new
project inherits. Empty, nothing is set -- the contract decides.
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


def _api_env(*surcharges: str) -> dict[str, str]:
    command = ["helm", "template", "choregos", str(CHART)]
    for surcharge in surcharges:
        command += ["--set", surcharge]
    rendu = subprocess.run(command, capture_output=True, text=True, check=False)
    assert rendu.returncode == 0, rendu.stderr
    documents: list[dict[str, Any]] = [d for d in yaml.safe_load_all(rendu.stdout) if d]
    deploiements = [d for d in documents if d.get("kind") == "Deployment"]
    (api,) = [d for d in deploiements if d["metadata"]["name"] == "choregos-api"]
    (conteneur,) = [c for c in api["spec"]["template"]["spec"]["containers"] if c["name"] == "api"]
    return {e["name"]: e.get("value", "") for e in conteneur.get("env", []) if "value" in e}


def test_nothing_by_default() -> None:
    env = _api_env()
    assert not {"CHOREGOS_DEFAULT_AGENT_BACKEND", "CHOREGOS_ALLOWED_AGENT_BACKENDS"} & env.keys()
    assert "CHOREGOS_DEFAULT_MODEL_PROFILES" not in env


def test_the_backend_and_profiles_of_the_deployment_reach_the_api() -> None:
    env = _api_env(
        "global.agents.defaultBackend=opencode",
        "global.agents.allowedBackends={opencode}",
        "global.agents.modelProfiles.standard=platform/standard",
    )
    assert env["CHOREGOS_DEFAULT_AGENT_BACKEND"] == "opencode"
    assert json.loads(env["CHOREGOS_ALLOWED_AGENT_BACKENDS"]) == ["opencode"]
    assert json.loads(env["CHOREGOS_DEFAULT_MODEL_PROFILES"]) == {"standard": "platform/standard"}
