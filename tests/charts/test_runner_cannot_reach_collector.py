"""A runner pod never reaches the trace collector (#946, S40-20d).

The runner pods run the agent's code. With the collector open to them, that code could write any
span to the platform's trace store: forge the gateway's spans and so a false `trace.drift`, or
carry data out past the run's egress proxy (ADR 0070). The API, the orchestrator and the nightly
drift job still reach it through their own policies.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"
RUNNER = "choregos-runner"
COLLECTOR_PORTS = {4317, 4318}

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _policies(*overrides: str) -> list[dict[str, Any]]:
    command = ["helm", "template", "choregos", str(CHART), "-n", "choregos"]
    for override in overrides:
        command += ["--set", override]
    rendered = subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)
    assert rendered.returncode == 0, rendered.stderr
    return [d for d in yaml.safe_load_all(rendered.stdout) if d and d.get("kind") == "NetworkPolicy"]


def _selects_runner(policy: dict[str, Any]) -> bool:
    """Whether the policy's egress rules apply to a pod labelled `app.kubernetes.io/name: choregos-runner`."""
    spec = policy["spec"]
    if "Egress" not in spec.get("policyTypes", []):
        return False
    selector = spec.get("podSelector") or {}
    labels = selector.get("matchLabels") or {}
    if labels and labels != {"app.kubernetes.io/name": RUNNER}:
        return False
    for expression in selector.get("matchExpressions") or []:
        if expression["key"] != "app.kubernetes.io/name":
            return False
        if expression["operator"] == "NotIn" and RUNNER in expression.get("values", []):
            return False
        if expression["operator"] == "In" and RUNNER not in expression.get("values", []):
            return False
    return True


def _opens_collector(rule: dict[str, Any]) -> bool:
    ports = {p.get("port") for p in rule.get("ports") or []}
    namespaces = {
        (peer.get("namespaceSelector") or {}).get("matchLabels", {}).get("kubernetes.io/metadata.name")
        for peer in rule.get("to") or []
    }
    return bool(ports & COLLECTOR_PORTS) or "monitoring" in namespaces


@pytest.mark.parametrize("overrides", [(), ("global.egress.enabled=true",)], ids=["default", "egress-proxy"])
def test_no_runner_policy_opens_the_collector(overrides: tuple[str, ...]) -> None:
    runner = [p for p in _policies(*overrides) if _selects_runner(p)]
    assert runner, "no NetworkPolicy selects the runner pods any more: this test would prove nothing"
    open_ = [
        (p["metadata"]["name"], rule)
        for p in runner
        for rule in p["spec"].get("egress") or []
        if _opens_collector(rule)
    ]
    assert not open_, f"a runner pod can reach the trace collector: {open_}"


def test_the_platform_pods_still_reach_the_collector() -> None:
    platform = {p["metadata"]["name"]: p for p in _policies()}["choregos-allow-platform"]
    assert any(_opens_collector(rule) for rule in platform["spec"]["egress"]), (
        "the API and the orchestrator lost the collector: their traces would be dropped"
    )
