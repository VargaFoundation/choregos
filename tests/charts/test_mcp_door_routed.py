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

ROOT = pathlib.Path(__file__).resolve().parents[2]
PATHS = ("/mcp", "/.well-known/oauth-protected-resource")
CHART = ROOT / "charts" / "choregos"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _documents(*surcharges: str) -> list[dict[str, Any]]:
    command = ["helm", "template", "choregos", str(CHART)]
    for surcharge in surcharges:
        command += ["--set", surcharge]
    rendu = subprocess.run(command, capture_output=True, text=True, check=True)
    return [doc for doc in yaml.safe_load_all(rendu.stdout) if doc]


def test_the_ingress_of_the_api_carries_mcp() -> None:
    ingress = next(
        d
        for d in _documents("choregos-api.ingress.kind=ingress")
        if d.get("kind") == "Ingress" and d["metadata"]["name"] == "choregos-api"
    )
    paths = [p["path"] for p in ingress["spec"]["rules"][0]["http"]["paths"]]
    assert set(PATHS) <= set(paths), paths


def test_the_http_route_of_the_api_carries_mcp() -> None:
    route = next(
        d for d in _documents() if d.get("kind") == "HTTPRoute" and d["metadata"]["name"] == "choregos-api"
    )
    prefixes = [m["path"]["value"] for rule in route["spec"]["rules"] for m in rule["matches"]]
    assert set(PATHS) <= set(prefixes), prefixes
