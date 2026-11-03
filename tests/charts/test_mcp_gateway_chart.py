# SPDX-License-Identifier: Apache-2.0
"""`global.mcpGateway` opens the API and the workers to the MCP gateway, never the runners
(S46-12, ADR 0105). Rendered with Helm and read in Python."""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART = ROOT / "charts" / "choregos"
POLICY = "choregos-mcp-gateway-egress"
VAR = "CHOREGOS_MCP_GATEWAY_INTERNAL_HOSTS"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _helm(*sets: str) -> subprocess.CompletedProcess[str]:
    command = ["helm", "template", "choregos", str(CHART)]
    for value in sets:
        command += ["--set-json", value]
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _render(*sets: str) -> list[dict[str, object]]:
    done = _helm(*sets)
    assert done.returncode == 0, done.stderr[-600:]
    return [doc for doc in yaml.safe_load_all(done.stdout) if doc]


def _env(docs: list[dict[str, object]], deployment: str) -> dict[str, str]:
    for doc in docs:
        if doc.get("kind") == "Deployment" and str(doc["metadata"]["name"]).endswith(deployment):  # type: ignore[index]
            containers = doc["spec"]["template"]["spec"]["containers"]  # type: ignore[index]
            return {e["name"]: e.get("value", "") for c in containers for e in (c.get("env") or [])}
    raise AssertionError(f"no Deployment named *{deployment}")


def _policy(docs: list[dict[str, object]], name: str) -> dict[str, object] | None:
    for doc in docs:
        if doc.get("kind") == "NetworkPolicy" and doc["metadata"]["name"] == name:  # type: ignore[index]
            return doc
    return None


def test_nothing_by_default() -> None:
    docs = _render()
    assert _policy(docs, POLICY) is None
    for deployment in ("api", "orchestrator"):
        env = _env(docs, deployment)
        assert VAR not in env
        assert "CHOREGOS_EGRESS_CEILING" not in env


def test_an_in_cluster_gateway_opens_the_api_and_the_workers_only() -> None:
    gateway = '{"namespace": "mcp", "podLabels": {"app": "gw"}, "ports": [8080]}'
    default = _render()
    docs = _render(f"global.mcpGateway={gateway}")
    policy = _policy(docs, POLICY)
    assert policy is not None
    spec = policy["spec"]  # type: ignore[index]
    selector = spec["podSelector"]["matchExpressions"]
    assert [e["values"] for e in selector] == [["choregos-api", "choregos-orchestrator"]]
    (rule,) = spec["egress"]
    (peer,) = rule["to"]
    assert peer["namespaceSelector"]["matchLabels"] == {"kubernetes.io/metadata.name": "mcp"}
    assert peer["podSelector"]["matchLabels"] == {"app": "gw"}
    assert rule["ports"] == [{"protocol": "TCP", "port": 8080}]
    assert _policy(docs, "choregos-runner-egress") == _policy(default, "choregos-runner-egress")
    assert _policy(docs, "choregos-allow-platform") == _policy(default, "choregos-allow-platform")
    # Plugin servers (ADR 0096) are never selected by the gateway policy.
    plugins = _render(f"global.mcpGateway={gateway}", "global.plugins.servers.enabled=true")
    again = _policy(plugins, POLICY)
    assert again is not None
    values = again["spec"]["podSelector"]["matchExpressions"][0]["values"]  # type: ignore[index]
    assert values == ["choregos-api", "choregos-orchestrator"]
    only_plugins = _render("global.plugins.servers.enabled=true")
    assert _policy(plugins, "choregos-allow-platform") == _policy(only_plugins, "choregos-allow-platform")


def test_an_external_gateway_is_opened_by_cidr() -> None:
    docs = _render('global.mcpGateway={"cidrs": ["203.0.113.0/24"]}')
    policy = _policy(docs, POLICY)
    assert policy is not None
    (rule,) = policy["spec"]["egress"]  # type: ignore[index]
    assert rule["to"] == [{"ipBlock": {"cidr": "203.0.113.0/24"}}]
    assert rule["ports"] == [{"protocol": "TCP", "port": 443}]


def test_internal_hosts_reach_the_api_and_the_workers() -> None:
    docs = _render('global.mcpGateway={"internalHosts": ["gw.mcp.svc"]}')
    for deployment in ("api", "orchestrator"):
        assert _env(docs, deployment)[VAR] == '["gw.mcp.svc"]'


def test_the_ceiling_gains_the_gateway_only_for_declared_families() -> None:
    docs = _render(
        'global.egress.ceiling={"api": ["api.github.com"], "runners": ["github.com"]}',
        'global.mcpGateway={"hosts": ["gw.example.test"]}',
    )
    for deployment in ("api", "orchestrator"):
        ceiling = json.loads(_env(docs, deployment)["CHOREGOS_EGRESS_CEILING"])
        assert ceiling == {
            "api": ["api.github.com", "gw.example.test"],
            "runners": ["github.com"],
        }


def test_extra_env_cannot_set_the_variable_twice() -> None:
    extra = f'global.extraEnv=[{{"name": "{VAR}", "value": "x"}}]'
    clash = _helm('global.mcpGateway={"internalHosts": ["gw.mcp.svc"]}', extra)
    assert clash.returncode != 0
    assert VAR in clash.stderr
    assert _helm(extra).returncode == 0
