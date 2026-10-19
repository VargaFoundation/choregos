"""The per-run egress proxy in the chart, and the runner's NetworkPolicies (ADR 0070)."""

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


def _render(*overrides: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    command = ["helm", "template", "choregos", str(CHART), "-n", "choregos"]
    for override in overrides:
        command += ["--set", override]
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)


def _docs(*overrides: str) -> dict[tuple[str, str], dict[str, Any]]:
    rendered = _render(*overrides)
    assert rendered.returncode == 0, rendered.stderr
    return {(d["kind"], d["metadata"]["name"]): d for d in yaml.safe_load_all(rendered.stdout) if d}


def _env(doc: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for container in doc["spec"]["template"]["spec"]["containers"]:
        for variable in container.get("env", []):
            if "value" in variable:
                values[variable["name"]] = variable["value"]
    return values


def _orchestrator_env(docs: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, str]]:
    return [_env(d) for (kind, name), d in docs.items() if kind == "Deployment" and "orchestrator" in name]


def test_disabled_by_default_nothing_is_rendered_and_nothing_changes_for_the_workers() -> None:
    docs = _docs()
    assert ("Deployment", "choregos-egress") not in docs and ("Service", "choregos-egress") not in docs
    assert ("NetworkPolicy", "choregos-egress-net") not in docs
    for env in _orchestrator_env(docs):
        assert "CHOREGOS_RUNNER_EGRESS_AUTH" not in env
    api = _env(docs[("Deployment", "choregos-api")])
    assert "CHOREGOS_NETWORK_INSTANCE_DENY" not in api


def test_enabled_renders_a_restricted_deployment_and_a_service_named_choregos_egress() -> None:
    docs = _docs("global.egress.enabled=true")
    deployment = docs[("Deployment", "choregos-egress")]
    labels = deployment["metadata"]["labels"]
    assert labels["app.kubernetes.io/name"] == "choregos-egress"
    pod = deployment["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    container = pod["containers"][0]
    assert container["command"] == ["python", "-m", "choregos_api.egress"]
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}
    assert container["resources"]["limits"] == {"cpu": "500m", "memory": "256Mi"}
    assert container["image"].startswith("ghcr.io/vargafoundation/choregos-api:")
    env = _env(deployment)
    assert env["CHOREGOS_EGRESS_API_URL"] == "http://choregos-api:8000/api/v1"
    assert json.loads(env["CHOREGOS_EGRESS_PORTS"]) == [443, 80]
    assert {p["name"] for p in container["ports"]} == {"proxy", "health"}
    template_labels = deployment["spec"]["template"]["metadata"]["labels"]
    assert "app.kubernetes.io/part-of" not in template_labels, (
        "choregos-allow-ingress must not admit all pods to it"
    )
    service = docs[("Service", "choregos-egress")]
    assert service["spec"]["ports"][0]["port"] == 3128
    assert service["spec"]["selector"]["app.kubernetes.io/name"] == "choregos-egress"


def test_enabled_tells_the_runners_to_authenticate_and_carries_the_deployments_deny_list() -> None:
    docs = _docs("global.egress.enabled=true", "global.egress.denyDomains={x.example,y.example}")
    for env in _orchestrator_env(docs):
        assert env["CHOREGOS_RUNNER_EGRESS_AUTH"] == "run"
        assert env["CHOREGOS_NETWORK_INSTANCE_DENY"] == "x.example,y.example"
    assert (
        _env(docs[("Deployment", "choregos-api")])["CHOREGOS_NETWORK_INSTANCE_DENY"] == "x.example,y.example"
    )
    assert json.loads(_env(docs[("Deployment", "choregos-egress")])["CHOREGOS_EGRESS_DENY_DOMAINS"]) == [
        "x.example",
        "y.example",
    ]


def test_the_guard_refuses_a_proxy_nobody_would_use() -> None:
    refused = _render("global.egress.enabled=true", "choregos-orchestrator.runner.egressProxy=")
    assert refused.returncode != 0
    assert "global.egress.enabled" in refused.stderr and "runner.egressProxy" in refused.stderr
    assert _render("global.egress.enabled=false", "choregos-orchestrator.runner.egressProxy=").returncode == 0


def _egress_rules(policy: dict[str, Any]) -> list[dict[str, Any]]:
    return list(policy["spec"].get("egress", []))


def _namespaces(rules: list[dict[str, Any]]) -> set[str]:
    return {
        to["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"]
        for rule in rules
        for to in rule["to"]
        if "namespaceSelector" in to
    }


def test_the_platform_policy_no_longer_selects_runner_pods_or_the_proxy() -> None:
    docs = _docs("global.egress.enabled=true")
    selector = docs[("NetworkPolicy", "choregos-allow-platform")]["spec"]["podSelector"]
    expression = selector["matchExpressions"][0]
    assert expression["key"] == "app.kubernetes.io/name" and expression["operator"] == "NotIn"
    assert set(expression["values"]) == {"choregos-runner", "choregos-egress"}


def test_a_runner_reaches_the_api_the_gateway_and_the_proxy_but_not_the_database_or_temporal() -> None:
    docs = _docs("global.egress.enabled=true")
    runner = docs[("NetworkPolicy", "choregos-runner-egress")]
    assert runner["spec"]["podSelector"]["matchLabels"] == {"app.kubernetes.io/name": "choregos-runner"}
    rules = _egress_rules(runner)
    assert _namespaces(rules).isdisjoint({"choregos-data", "choregos-temporal"})
    assert {"kube-system", "choregos-gateway", "choregos-memory"} <= _namespaces(rules)
    pods = {
        (to["podSelector"]["matchLabels"]["app.kubernetes.io/name"], port["port"])
        for rule in rules
        for to in rule["to"]
        if "podSelector" in to
        for port in rule["ports"]
    }
    assert pods == {("choregos-api", 8000), ("choregos-egress", 3128)}
    assert not any("ipBlock" in to for rule in rules for to in rule["to"]), "no direct way out"


def test_without_the_proxy_a_runner_has_no_way_out_and_still_no_database() -> None:
    docs = _docs()
    runner = docs[("NetworkPolicy", "choregos-runner-egress")]
    pods = {
        to["podSelector"]["matchLabels"]["app.kubernetes.io/name"]
        for rule in _egress_rules(runner)
        for to in rule["to"]
        if "podSelector" in to
    }
    assert pods == {"choregos-api"}
    assert _namespaces(_egress_rules(runner)).isdisjoint({"choregos-data", "choregos-temporal"})


def test_the_proxy_takes_traffic_from_runners_only_and_reaches_the_internet_minus_private_ranges() -> None:
    docs = _docs("global.egress.enabled=true", "global.egress.ports={443}")
    net = docs[("NetworkPolicy", "choregos-egress-net")]
    assert net["spec"]["podSelector"]["matchLabels"] == {"app.kubernetes.io/name": "choregos-egress"}
    (ingress,) = net["spec"]["ingress"]
    assert ingress["from"] == [
        {"podSelector": {"matchLabels": {"app.kubernetes.io/name": "choregos-runner"}}}
    ]
    assert ingress["ports"] == [{"protocol": "TCP", "port": 3128}]
    internet = [r for r in _egress_rules(net) if any("ipBlock" in to for to in r["to"])]
    (rule,) = internet
    block = rule["to"][0]["ipBlock"]
    assert block["cidr"] == "0.0.0.0/0"
    assert {"10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"} <= set(block["except"])
    assert rule["ports"] == [{"protocol": "TCP", "port": 443}]
    api = [r for r in _egress_rules(net) if any("podSelector" in to for to in r["to"])]
    assert api and api[0]["ports"] == [{"protocol": "TCP", "port": 8000}]


def test_network_policies_off_renders_none_of_them() -> None:
    docs = _docs("global.egress.enabled=true", "networkPolicies.enabled=false")
    # `choregos-runner-assignment` (ADR 0053) is the sandbox executor's own, rendered whatever
    # `networkPolicies.enabled` says; agent-sandbox is the default since ADR 0072.
    assert not [k for k in docs if k[0] == "NetworkPolicy" and k[1] != "choregos-runner-assignment"]
    assert ("Deployment", "choregos-egress") in docs
