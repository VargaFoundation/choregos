"""The CLI the instance serves (S62-08) exits through the API, under both of the chart's entry forms.

Without these paths the Ingress sends `curl <instance>/cli/install.sh` to the console, which answers
its HTML 404 page, and `sh` reads that page as a script.
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


def test_the_api_ingress_carries_the_downloads_and_the_install_scripts() -> None:
    ingress = next(
        d
        for d in _documents("choregos-api.ingress.kind=ingress")
        if d.get("kind") == "Ingress" and d["metadata"]["name"] == "choregos-api"
    )
    paths = {p["path"]: p["pathType"] for p in ingress["spec"]["rules"][0]["http"]["paths"]}
    assert paths.get("/downloads") == "Prefix", paths
    # Exact: the rest of `/cli` stays the console's.
    assert paths.get("/cli/install.sh") == "Exact", paths
    assert paths.get("/cli/install.ps1") == "Exact", paths
    assert "/cli" not in paths


def test_the_api_http_route_carries_the_downloads_and_the_install_scripts() -> None:
    route = next(
        d for d in _documents() if d.get("kind") == "HTTPRoute" and d["metadata"]["name"] == "choregos-api"
    )
    matches = {
        m["path"]["value"]: m["path"]["type"] for rule in route["spec"]["rules"] for m in rule["matches"]
    }
    assert matches.get("/downloads") == "PathPrefix", matches
    assert matches.get("/cli/install.sh") == "Exact", matches
    assert matches.get("/cli/install.ps1") == "Exact", matches
