"""S35-03 — suspend and resume on a real agent-sandbox operator, and the bound.

What the `agent_sandbox` executor relies on, checked where it runs:

- a suspended sandbox loses its pod and keeps its volume; resumed, a NEW pod finds the
  volume as it was left (the workspace an agent wrote before asking its question);
- a claim whose `shutdownTime` passes while its sandbox is suspended is deleted with its
  sandbox: the bound `sandbox.suspend_max_hours` is held by the controller, not by us.

What it does not show: that an answered question costs one run, not two — that is the
orchestrator's, proved on the fakes (`apps/orchestrator/tests/test_suspend_around_human.py`);
proving it here needs the whole platform deployed with this executor on the cluster.

Non-destructive (a namespace of its own): `CHOREGOS_CLUSTER_ALLOW_SHARED=<context>`.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from .conftest import kubectl_shared
from .test_agent_sandbox import _condition, _get, _wait, operator_installed

pytestmark = pytest.mark.cluster

IMAGE = "busybox:1.36"
#: Writes its birth once on the volume, then prints it: a resumed pod prints the SAME line.
SCRIPT = "test -f /data/born || date +%s%N > /data/born; cat /data/born; sleep 3600"


@pytest.fixture(scope="module")
def ns() -> Iterator[str]:
    operator_installed()
    name = f"choregos-sandbox-suspend-{secrets.token_hex(3)}"
    kubectl_shared("create", "namespace", name)
    try:
        yield name
    finally:
        kubectl_shared("delete", "namespace", name, "--wait=false", "--ignore-not-found", check=False)


def _pod_spec() -> dict[str, Any]:
    return {
        "automountServiceAccountToken": False,
        "restartPolicy": "Never",
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": 65534,
            "fsGroup": 65534,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "containers": [
            {
                "name": "runner",
                "image": IMAGE,
                "command": ["sh", "-c", SCRIPT],
                "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
                "volumeMounts": [{"name": "workspace", "mountPath": "/data"}],
            }
        ],
    }


def _volumes() -> list[dict[str, Any]]:
    return [
        {
            "metadata": {"name": "workspace"},
            "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "64Mi"}}},
        }
    ]


def _mode(ns: str, sandbox: str, mode: str) -> None:
    kubectl_shared(
        "-n", ns, "patch", "sandbox", sandbox, "--type", "merge", "-p",
        json.dumps({"spec": {"operatingMode": mode}}),
    )  # fmt: skip


def _ready(ns: str, sandbox: str) -> bool:
    return _condition(_get(ns, "sandbox", sandbox), "Ready").get("status") == "True"


def _logs(ns: str, pod: str) -> str:
    return kubectl_shared("-n", ns, "logs", pod, check=False).strip()


def test_a_resumed_sandbox_finds_its_volume_as_it_was_left(ns: str) -> None:
    sandbox = {
        "apiVersion": "agents.x-k8s.io/v1beta1",
        "kind": "Sandbox",
        "metadata": {"name": "asked", "namespace": ns},
        "spec": {"podTemplate": {"spec": _pod_spec()}, "volumeClaimTemplates": _volumes()},
    }
    kubectl_shared("apply", "-f", "-", input_text=json.dumps(sandbox))
    _wait(lambda: _ready(ns, "asked"), "the sandbox to be Ready", timeout_s=300)
    _wait(lambda: bool(_logs(ns, "asked")), "its first words")
    born = _logs(ns, "asked")
    first_uid = (_get(ns, "pod", "asked") or {})["metadata"]["uid"]

    _mode(ns, "asked", "Suspended")
    _wait(lambda: _get(ns, "pod", "asked") is None, "the pod to go")
    _wait(
        lambda: _condition(_get(ns, "sandbox", "asked"), "Suspended").get("status") == "True",
        "the Suspended condition",
    )
    claims = json.loads(kubectl_shared("-n", ns, "get", "pvc", "-o", "json"))["items"]
    assert claims, "the workspace volume went with the pod"

    _mode(ns, "asked", "Running")
    _wait(lambda: _ready(ns, "asked"), "the sandbox to be Ready again", timeout_s=300)
    _wait(lambda: bool(_logs(ns, "asked")), "the new pod's first words")
    assert (_get(ns, "pod", "asked") or {})["metadata"]["uid"] != first_uid, "a NEW pod"
    assert _logs(ns, "asked") == born, "the resumed pod read what the first one wrote"


def test_past_its_shutdown_time_a_suspended_claim_is_gone(ns: str) -> None:
    """The executor puts `sandbox.suspend_max_hours` in the claim's `shutdownTime` when it
    suspends; here the bound is 45 seconds."""
    template = {
        "apiVersion": "extensions.agents.x-k8s.io/v1beta1",
        "kind": "SandboxTemplate",
        "metadata": {"name": "bounded", "namespace": ns},
        "spec": {
            "networkPolicyManagement": "Unmanaged",
            "podTemplate": {"spec": _pod_spec()},
            "volumeClaimTemplates": _volumes(),
        },
    }
    pool = {
        "apiVersion": "extensions.agents.x-k8s.io/v1beta1",
        "kind": "SandboxWarmPool",
        "metadata": {"name": "bounded", "namespace": ns},
        "spec": {"replicas": 0, "sandboxTemplateRef": {"name": "bounded"}},
    }
    shutdown = (datetime.now(UTC) + timedelta(seconds=45)).strftime("%Y-%m-%dT%H:%M:%SZ")
    claim = {
        "apiVersion": "extensions.agents.x-k8s.io/v1beta1",
        "kind": "SandboxClaim",
        "metadata": {"name": "run-bounded", "namespace": ns},
        "spec": {
            "warmPoolRef": {"name": "bounded"},
            "lifecycle": {"shutdownTime": shutdown, "shutdownPolicy": "Delete"},
        },
    }
    for manifest in (template, pool, claim):
        kubectl_shared("apply", "-f", "-", input_text=json.dumps(manifest))
    _wait(
        lambda: (
            ((_get(ns, "sandboxclaim", "run-bounded") or {}).get("status") or {})
            .get("sandbox", {})
            .get("name")
        ),
        "the claim to be bound",
        timeout_s=300,
    )
    sandbox = (_get(ns, "sandboxclaim", "run-bounded") or {})["status"]["sandbox"]["name"]
    _mode(ns, sandbox, "Suspended")
    _wait(lambda: _get(ns, "sandboxclaim", "run-bounded") is None, "the claim to expire", timeout_s=240)
    _wait(lambda: _get(ns, "sandbox", sandbox) is None, "its sandbox to be deleted", timeout_s=240)
