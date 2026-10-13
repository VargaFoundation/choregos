"""S35-01 — the agent-sandbox operator, on a real cluster: a Sandbox is created, then deleted.

The bench (kind) has no agent-sandbox operator; this runs where a platform team installed it
(`infra/bootstrap/agent-sandbox.yaml`, pinned in `infra/operators/agent-sandbox/`). It is
NON-destructive — one namespace of its own, deleted at the end — so it may run on a shared
cluster named explicitly:

    CHOREGOS_CLUSTER_CONTEXT=dev CHOREGOS_CLUSTER_ALLOW_SHARED=dev make test-cluster

Without the operator it is skipped, and says so: a skipped proof is not a proof.
"""

from __future__ import annotations

import json
import secrets
import shutil
import subprocess
import time
from collections.abc import Iterator
from typing import Any

import pytest

from .conftest import kubectl_shared

pytestmark = pytest.mark.cluster

#: The API the executor speaks. Another served version means the operator moved under us.
PINNED = {
    "sandboxes.agents.x-k8s.io": "v1beta1",
    "sandboxclaims.extensions.agents.x-k8s.io": "v1beta1",
    "sandboxtemplates.extensions.agents.x-k8s.io": "v1beta1",
    "sandboxwarmpools.extensions.agents.x-k8s.io": "v1beta1",
}
IMAGE = "registry.k8s.io/pause:3.10"


def operator_installed() -> None:
    found = kubectl_shared("get", "crd", "-o", "name", check=False)
    missing = [crd for crd in PINNED if f"customresourcedefinition.apiextensions.k8s.io/{crd}" not in found]
    if missing:
        pytest.skip(
            f"agent-sandbox operator not installed ({', '.join(missing)} absent): "
            "install infra/operators/agent-sandbox (v1.0.6) through the cluster's GitOps first"
        )


@pytest.fixture(scope="module")
def namespace() -> Iterator[str]:
    operator_installed()
    name = f"choregos-sandbox-test-{secrets.token_hex(3)}"
    kubectl_shared("create", "namespace", name)
    try:
        yield name
    finally:
        kubectl_shared("delete", "namespace", name, "--wait=false", "--ignore-not-found", check=False)


def _get(namespace: str, kind: str, name: str) -> dict[str, Any] | None:
    out = kubectl_shared("-n", namespace, "get", kind, name, "-o", "json", "--ignore-not-found", check=False)
    return json.loads(out) if out.strip() else None


def _condition(obj: dict[str, Any] | None, kind: str) -> dict[str, Any]:
    for condition in ((obj or {}).get("status") or {}).get("conditions") or []:
        if condition.get("type") == kind:
            return dict(condition)
    return {}


def _wait(predicate: Any, what: str, timeout_s: float = 180) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(2)
    raise AssertionError(f"timed out after {timeout_s:.0f} s waiting for {what}")


def _sandbox(name: str, namespace: str) -> dict[str, Any]:
    return {
        "apiVersion": "agents.x-k8s.io/v1beta1",
        "kind": "Sandbox",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "podTemplate": {
                "spec": {
                    "automountServiceAccountToken": False,
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 65535,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": "pause",
                            "image": IMAGE,
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "capabilities": {"drop": ["ALL"]},
                            },
                        }
                    ],
                }
            }
        },
    }


def test_the_operator_serves_the_pinned_api() -> None:
    operator_installed()
    for crd, version in PINNED.items():
        served = kubectl_shared(
            "get", "crd", crd, "-o", "jsonpath={.spec.versions[?(@.served==true)].name}"
        ).split()
        assert version in served, f"{crd} serves {served}, the executor speaks {version}"


def test_a_sandbox_is_created_then_deleted(namespace: str) -> None:
    kubectl_shared("apply", "-f", "-", input_text=json.dumps(_sandbox("probe", namespace)))
    _wait(
        lambda: _condition(_get(namespace, "sandbox", "probe"), "Ready").get("status") == "True",
        "the Sandbox to be Ready",
    )
    assert _get(namespace, "pod", "probe") is not None, "Ready without a pod named after the sandbox"

    kubectl_shared("-n", namespace, "delete", "sandbox", "probe", "--wait=false")
    _wait(lambda: _get(namespace, "sandbox", "probe") is None, "the Sandbox to be gone")
    _wait(lambda: _get(namespace, "pod", "probe") is None, "its pod to be gone")


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")
def test_the_chart_template_and_pool_are_accepted(namespace: str) -> None:
    """The chart's SandboxTemplate and SandboxWarmPool, as the API server judges them
    (server-side dry run: validated, defaulted, admitted, and not stored)."""
    rendered = subprocess.run(
        [
            "helm", "template", "choregos", "charts/choregos", "--namespace", namespace,
            "--set", "choregos-orchestrator.runner.executor=agent_sandbox",
            "--set", f"choregos-orchestrator.runner.namespacePattern={namespace}",
            "--show-only", "charts/choregos-orchestrator/templates/sandbox.yaml",
        ],
        capture_output=True, text=True, check=True, timeout=120,
    ).stdout  # fmt: skip
    out = kubectl_shared("-n", namespace, "apply", "--dry-run=server", "-f", "-", input_text=rendered)
    assert "sandboxtemplate.extensions.agents.x-k8s.io/choregos-runner" in out
    assert "sandboxwarmpool.extensions.agents.x-k8s.io/choregos-runner" in out
