"""The MCP gate accepts the IdP's tokens when the deployment says so (ADR 0030).

`global.mcp.oauth` becomes the API's settings: before, variables had to be set by hand
in `global.extraEnv`, and the operations guide showed a `choregos-api.env` the chart
never read. The clients become the JSON the API expects -- `client_id`, `callback_port`.
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


def _rendu(*surcharges: str) -> subprocess.CompletedProcess[str]:
    commande = ["helm", "template", "choregos", str(CHART)]
    for surcharge in surcharges:
        commande += ["--set", surcharge]
    return subprocess.run(commande, capture_output=True, text=True, check=False)


def _env_de_l_api(*surcharges: str) -> dict[str, str]:
    rendu = _rendu(*surcharges)
    assert rendu.returncode == 0, rendu.stderr
    documents: list[dict[str, Any]] = [d for d in yaml.safe_load_all(rendu.stdout) if d]
    deploiements = [d for d in documents if d.get("kind") == "Deployment"]
    (api,) = [d for d in deploiements if d["metadata"]["name"] == "choregos-api"]
    (conteneur,) = [c for c in api["spec"]["template"]["spec"]["containers"] if c["name"] == "api"]
    return {e["name"]: e.get("value", "") for e in conteneur.get("env", []) if "value" in e}


def test_eteinte_par_defaut() -> None:
    assert not [nom for nom in _env_de_l_api() if nom.startswith("CHOREGOS_MCP_OAUTH_")]


def test_allumee_avec_ses_clients() -> None:
    env = _env_de_l_api(
        "global.mcp.oauth.enabled=true",
        "global.mcp.oauth.clients.claude-code.clientId=choregos-claude-code",
        "global.mcp.oauth.clients.claude-code.callbackPort=33418",
        "global.mcp.oauth.clients.claude-ai.clientId=choregos-claude-ai",
    )
    assert env["CHOREGOS_MCP_OAUTH_ENABLED"] == "true"
    assert env["CHOREGOS_MCP_OAUTH_AUDIENCE"] == "choregos-mcp"
    assert "CHOREGOS_MCP_OAUTH_ISSUER" not in env, "empty: the console's issuer"
    assert json.loads(env["CHOREGOS_MCP_OAUTH_CLIENTS"]) == {
        "claude-ai": {"client_id": "choregos-claude-ai", "callback_port": None},
        "claude-code": {"client_id": "choregos-claude-code", "callback_port": 33418},
    }


def test_l_api_lit_ce_que_le_chart_ecrit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rendered JSON is what the API settings accept -- not merely JSON."""
    from choregos_api.config import Settings

    env = _env_de_l_api(
        "global.mcp.oauth.enabled=true",
        "global.mcp.oauth.issuer=https://idp.example/realms/autre",
        "global.mcp.oauth.clients.claude-code.clientId=choregos-claude-code",
        "global.mcp.oauth.clients.claude-code.callbackPort=33418",
    )
    for nom, valeur in env.items():
        if nom.startswith("CHOREGOS_MCP_OAUTH_"):
            monkeypatch.setenv(nom, valeur)
    reglages = Settings()
    assert reglages.mcp_oauth_enabled is True
    assert reglages.mcp_oauth_issuer == "https://idp.example/realms/autre"
    assert reglages.mcp_oauth_clients["claude-code"].callback_port == 33418


def test_une_variable_posee_deux_fois_est_refusee() -> None:
    rendu = _rendu(
        "global.mcp.oauth.enabled=true",
        "global.extraEnv[0].name=CHOREGOS_MCP_OAUTH_AUDIENCE",
        "global.extraEnv[0].value=autre",
    )
    assert rendu.returncode != 0
    assert "CHOREGOS_MCP_OAUTH_AUDIENCE" in rendu.stderr
