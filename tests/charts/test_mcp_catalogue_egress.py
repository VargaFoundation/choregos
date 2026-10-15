"""The deployment tells the API which hosts it may reach and mounts the operator's own MCP catalogue.

`choregos-api.egressHosts` becomes `CHOREGOS_API_EGRESS_HOSTS` (a JSON list);
`choregos-api.mcpCatalogueExtra` names a ConfigMap, mounted read-only, whose `catalogue.yaml` is
merged after the shipped catalogue (ADR 0065). Both empty: nothing is set and the catalogue answers
`unknown` for egress.
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


def _api_pod(*overrides: str) -> dict[str, Any]:
    command = ["helm", "template", "choregos", str(CHART)]
    for override in overrides:
        command += ["--set", override]
    rendered = subprocess.run(command, capture_output=True, text=True, check=False)
    assert rendered.returncode == 0, rendered.stderr
    documents: list[dict[str, Any]] = [d for d in yaml.safe_load_all(rendered.stdout) if d]
    (api,) = [
        d for d in documents if d.get("kind") == "Deployment" and d["metadata"]["name"] == "choregos-api"
    ]
    pod: dict[str, Any] = api["spec"]["template"]["spec"]
    return pod


def _env(pod: dict[str, Any]) -> dict[str, str]:
    (container,) = [c for c in pod["containers"] if c["name"] == "api"]
    return {e["name"]: e.get("value", "") for e in container.get("env", []) if "value" in e}


def test_nothing_is_set_by_default() -> None:
    pod = _api_pod()
    env = _env(pod)
    assert "CHOREGOS_API_EGRESS_HOSTS" not in env and "CHOREGOS_MCP_CATALOGUE_EXTRA" not in env
    assert "mcp-catalogue" not in {v["name"] for v in pod["volumes"]}


def test_the_egress_hosts_reach_the_api_as_a_json_list() -> None:
    env = _env(_api_pod("choregos-api.egressHosts={api.githubcopilot.com,*.notion.com}"))
    assert json.loads(env["CHOREGOS_API_EGRESS_HOSTS"]) == ["api.githubcopilot.com", "*.notion.com"]


def test_the_site_catalogue_is_mounted_read_only_and_named_by_the_environment() -> None:
    pod = _api_pod("choregos-api.mcpCatalogueExtra=site-mcp-catalogue")
    assert _env(pod)["CHOREGOS_MCP_CATALOGUE_EXTRA"] == "/etc/choregos/mcp-catalogue/catalogue.yaml"
    (volume,) = [v for v in pod["volumes"] if v["name"] == "mcp-catalogue"]
    assert volume["configMap"]["name"] == "site-mcp-catalogue"
    (container,) = [c for c in pod["containers"] if c["name"] == "api"]
    (mount,) = [m for m in container["volumeMounts"] if m["name"] == "mcp-catalogue"]
    assert mount["mountPath"] == "/etc/choregos/mcp-catalogue" and mount["readOnly"] is True
