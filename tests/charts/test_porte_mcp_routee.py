"""The MCP gate (ADR 0030) exits through the API, under both of the chart's entry forms.

Without the `/mcp` path, the Ingress sends an MCP client's request to the console, which renders its 404
page: the client shows "Couldn't reach the MCP server" without saying why. Without the OAuth
metadata path (RFC 9728), claude.ai does not find the IdP and does not offer to sign in.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

RACINE = pathlib.Path(__file__).resolve().parents[2]
CHEMINS = ("/mcp", "/.well-known/oauth-protected-resource")
CHART = RACINE / "charts" / "choregos"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _documents(*surcharges: str) -> list[dict[str, Any]]:
    commande = ["helm", "template", "choregos", str(CHART)]
    for surcharge in surcharges:
        commande += ["--set", surcharge]
    rendu = subprocess.run(commande, capture_output=True, text=True, check=True)
    return [doc for doc in yaml.safe_load_all(rendu.stdout) if doc]


def test_l_ingress_de_l_api_porte_mcp() -> None:
    ingress = next(
        d
        for d in _documents("choregos-api.ingress.kind=ingress")
        if d.get("kind") == "Ingress" and d["metadata"]["name"] == "choregos-api"
    )
    chemins = [p["path"] for p in ingress["spec"]["rules"][0]["http"]["paths"]]
    assert set(CHEMINS) <= set(chemins), chemins


def test_la_route_http_de_l_api_porte_mcp() -> None:
    route = next(
        d for d in _documents() if d.get("kind") == "HTTPRoute" and d["metadata"]["name"] == "choregos-api"
    )
    prefixes = [m["path"]["value"] for regle in route["spec"]["rules"] for m in regle["matches"]]
    assert set(CHEMINS) <= set(prefixes), prefixes
