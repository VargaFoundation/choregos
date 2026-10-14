"""S35-04 — each capability `agent_sandbox` announces, proved by the REAL executor on a cluster.

The executor runs here against the cluster's API through `kubectl proxy`, with the operator
doing its part. What it cannot do from outside the cluster is push an assignment to a pod IP;
that part is `test_agent_sandbox_executor.py`, which pushes from a pod.

Each test carries `@pytest.mark.capability(executor, name)`: the conformance suite
(`tests/conformance/executeurs/test_cluster_proofs.py`) refuses an executor that announces a
capability no cluster test carries — an announced, absent capability is worse than an absent one.
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import subprocess
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from choregos_adapters.executor.agent_sandbox import AgentSandboxExecutor
from choregos_adapters.executor.tekton import KubernetesClient
from choregos_contracts import ExecutorKind
from choregos_core.domain import ExecRef, StageJobSpec

from .conftest import context, kubectl_shared
from .test_agent_sandbox import _condition, _get, _wait, operator_installed

pytestmark = pytest.mark.cluster

POOL = "capabilities"


@pytest.fixture(scope="module")
def ns() -> Iterator[str]:
    operator_installed()
    name = f"choregos-sandbox-caps-{secrets.token_hex(3)}"
    kubectl_shared("create", "namespace", name)
    pod = {
        "automountServiceAccountToken": False,
        "restartPolicy": "Never",
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": 65535,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "containers": [
            {
                "name": "runner",
                "image": "registry.k8s.io/pause:3.10",
                "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
            }
        ],
    }
    for manifest in (
        {
            "apiVersion": "extensions.agents.x-k8s.io/v1beta1",
            "kind": "SandboxTemplate",
            "metadata": {"name": POOL, "namespace": name},
            "spec": {"networkPolicyManagement": "Unmanaged", "podTemplate": {"spec": pod}},
        },
        {
            "apiVersion": "extensions.agents.x-k8s.io/v1beta1",
            "kind": "SandboxWarmPool",
            "metadata": {"name": POOL, "namespace": name},
            "spec": {"replicas": 1, "sandboxTemplateRef": {"name": POOL}},
        },
    ):
        kubectl_shared("apply", "-f", "-", input_text=json.dumps(manifest))
    try:
        yield name
    finally:
        kubectl_shared("delete", "namespace", name, "--wait=false", "--ignore-not-found", check=False)


@pytest.fixture
async def executor(ns: str) -> AsyncIterator[AgentSandboxExecutor]:
    """The real executor, on the cluster's API through `kubectl proxy` (no token to handle)."""
    proxy = subprocess.Popen(
        ["kubectl", "--context", context(non_destructive=True), "proxy", "--port=0"],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proxy.stdout is not None
        port = re.search(r":(\d+)", proxy.stdout.readline())
        assert port, "kubectl proxy did not say where it serves"
        client = KubernetesClient(f"http://127.0.0.1:{port.group(1)}", token="", verify=False)
        yield AgentSandboxExecutor(client, warm_pool=POOL, max_active=1)
        await client.aclose()
    finally:
        proxy.terminate()


def _spec(ns: str, run_id: str, token: str = "token") -> StageJobSpec:
    return StageJobSpec(
        run_id=run_id,
        project_slug="caps",
        namespace=ns,
        runner_image="registry.k8s.io/pause:3.10",
        api_url="http://nowhere.invalid",
        run_token=token,
    )


def _ref(ns: str, run_id: str) -> ExecRef:
    return ExecRef(kind=ExecutorKind.AGENT_SANDBOX, name=f"run-{run_id}", namespace=ns, run_id=run_id)


def _token(ns: str, run_id: str) -> str:
    secret = _get(ns, "secret", f"run-{run_id}") or {}
    return base64.b64decode((secret.get("data") or {}).get("token", "")).decode()


def _bound(ns: str, run_id: str) -> str:
    claim = _get(ns, "sandboxclaim", f"run-{run_id}") or {}
    return str(((claim.get("status") or {}).get("sandbox") or {}).get("name") or "")


@pytest.mark.capability("agent_sandbox", "queue")
async def test_queue_beyond_the_cap_a_run_has_no_claim_and_waits_its_turn(
    ns: str, executor: AgentSandboxExecutor
) -> None:
    first = await executor.start(_spec(ns, "q1"))
    second = await executor.start(_spec(ns, "q2"))
    assert _get(ns, "sandboxclaim", "run-q1") is not None
    assert _get(ns, "sandboxclaim", "run-q2") is None, "beyond the cap: no claim, no pod"
    waiting = await executor.status(second)
    assert waiting.state == "pending" and "waiting for a slot" in waiting.message
    await executor.cancel(first)
    _wait(lambda: _get(ns, "sandboxclaim", "run-q1") is None, "the first claim to go")
    await executor.status(second)
    assert _get(ns, "sandboxclaim", "run-q2") is not None, "its turn came: it is claimed"
    await executor.cancel(second)


@pytest.mark.capability("agent_sandbox", "renew")
async def test_renew_replaces_the_token_in_the_runs_secret(ns: str, executor: AgentSandboxExecutor) -> None:
    ref = await executor.start(_spec(ns, "r1", token="old"))
    assert _token(ns, "r1") == "old"
    await executor.renew(ref, "fresh")
    assert _token(ns, "r1") == "fresh"
    await executor.cancel(ref)


@pytest.mark.capability("agent_sandbox", "snapshot")
async def test_snapshot_a_claimed_sandbox_is_adopted_from_the_pool_then_never_reused(
    ns: str, executor: AgentSandboxExecutor
) -> None:
    _wait(lambda: any(_condition(s, "Ready").get("status") == "True" for s in _sandboxes(ns)), "a warm one")
    warm = {s["metadata"]["name"] for s in _sandboxes(ns)}
    ref = await executor.start(_spec(ns, "s1"))
    _wait(lambda: bool(_bound(ns, "s1")), "the claim to be bound")
    used = _bound(ns, "s1")
    assert used in warm, "the claim adopted a sandbox the pool had made in advance"
    await executor.cancel(ref)
    _wait(lambda: _get(ns, "sandbox", used) is None, "the used sandbox to be deleted")
    _wait(lambda: any(s["metadata"]["name"] != used for s in _sandboxes(ns)), "the pool to make a new one")


@pytest.mark.capability("agent_sandbox", "suspend")
@pytest.mark.capability("agent_sandbox", "resume")
async def test_suspend_then_resume_the_same_sandbox(ns: str, executor: AgentSandboxExecutor) -> None:
    ref = await executor.start(_spec(ns, "p1"))
    _wait(lambda: bool(_bound(ns, "p1")), "the claim to be bound")
    sandbox = _bound(ns, "p1")
    until = datetime.now(UTC) + timedelta(hours=1)
    assert await executor.suspend(ref, until) is True
    _wait(lambda: _get(ns, "pod", sandbox) is None, "the pod to go")
    claim = _get(ns, "sandboxclaim", "run-p1") or {}
    assert "ttlSecondsAfterFinished" not in claim["spec"].get("lifecycle", {})
    assert (await executor.status(ref)).message.startswith("suspended")

    assert await executor.resume(ref, "fresh", until) is True
    _wait(lambda: _condition(_get(ns, "sandbox", sandbox), "Ready").get("status") == "True", "a new pod")
    assert _bound(ns, "p1") == sandbox, "the same sandbox, not another"
    assert _token(ns, "p1") == "fresh"
    await executor.cancel(ref)


def _sandboxes(ns: str) -> list[dict[str, Any]]:
    return list(json.loads(kubectl_shared("-n", ns, "get", "sandboxes", "-o", "json")).get("items", []))
