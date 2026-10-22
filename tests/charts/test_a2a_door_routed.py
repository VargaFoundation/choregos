"""The A2A door (ADR 0078) exits through the API, under both of the chart's entry forms.

Without the `/a2a` path, the Ingress sends an outside agent's card request to the console, which
answers its 404 page: the agent reads "no agent here" without saying why.
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

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _documents(*overrides: str) -> list[dict[str, Any]]:
    command = ["helm", "template", "choregos", str(CHART)]
    for override in overrides:
        command += ["--set", override]
    rendered = subprocess.run(command, capture_output=True, text=True, check=True)
    return [doc for doc in yaml.safe_load_all(rendered.stdout) if doc]


def test_the_api_ingress_carries_the_a2a_door() -> None:
    ingress = next(
        d
        for d in _documents("choregos-api.ingress.kind=ingress")
        if d.get("kind") == "Ingress" and d["metadata"]["name"] == "choregos-api"
    )
    paths = [p["path"] for p in ingress["spec"]["rules"][0]["http"]["paths"]]
    assert "/a2a" in paths, paths


def test_the_api_http_route_carries_the_a2a_door() -> None:
    route = next(
        d for d in _documents() if d.get("kind") == "HTTPRoute" and d["metadata"]["name"] == "choregos-api"
    )
    prefixes = [m["path"]["value"] for rule in route["spec"]["rules"] for m in rule["matches"]]
    assert "/a2a" in prefixes, prefixes
