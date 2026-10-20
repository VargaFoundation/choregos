"""S41-17 — a hosted run under the `gvisor` RuntimeClass, on a real cluster.

The same run as S41-16's (the reference agent as a native sidecar, a probe as the runner), with
the pod under gVisor: the whole pod is one sandbox, so the runner's `uname -r` is the sentry's
kernel, the agent still answers on the pod's loopback, and it still stops on SIGTERM through the
sentry. The policy that refuses a pod without the RuntimeClass is judged by the `kyverno` CLI in
`apps/orchestrator/tests/test_gvisor_policy.py`; the cluster of `make cluster-up` has no Kyverno.

It needs `make cluster-gvisor` (runsc in the runners node, the RuntimeClass) and the reference
image of S41-16's test (`CHOREGOS_HOSTED_REFERENCE_IMAGE`); without either, it is skipped.
"""

from __future__ import annotations

import asyncio
import dataclasses
import subprocess
import time

import pytest
from choregos_adapters.executor.k8s_job import AGENT_CONTAINER, KubernetesJobExecutor
from choregos_core.hosted import NATIVE_SIDECARS_MIN

from .conftest import context, kubectl_shared
from .test_hosted_native_sidecar import (  # noqa: F401 - `ns` and `executor` are fixtures
    RUNNER,
    _cluster_version,
    _pod,
    _reference_image,
    _spec,
    executor,
    ns,
)

pytestmark = pytest.mark.cluster


def _needs_the_runtime_class() -> None:
    found = subprocess.run(
        ["kubectl", "--context", context(non_destructive=True), "get", "runtimeclass", "gvisor"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if found.returncode != 0:
        pytest.skip("no `gvisor` RuntimeClass in this cluster: `make cluster-gvisor`")


async def test_a_hosted_run_finishes_under_gvisor(ns: str, executor: KubernetesJobExecutor) -> None:  # noqa: F811
    image = _reference_image()
    _needs_the_runtime_class()
    if await _cluster_version(executor) < NATIVE_SIDECARS_MIN:
        pytest.skip("the cluster is older than 1.29: no native sidecar")
    spec = dataclasses.replace(
        _spec(ns, image), runtime_class="gvisor", runner_command=["sh", "-c", f"uname -r; {RUNNER}"]
    )
    ref = await executor.start(spec)
    state, deadline = "pending", time.monotonic() + 300
    while time.monotonic() < deadline:
        state = (await executor.status(ref)).state
        if state in {"succeeded", "failed"}:
            break
        await asyncio.sleep(3)
    assert state == "succeeded", f"the hosted run did not finish under gVisor: {state}"

    pod = _pod(ns, spec.run_id)
    assert pod["spec"]["runtimeClassName"] == "gvisor"
    runner_log = kubectl_shared("-n", ns, "logs", pod["metadata"]["name"], "-c", "runner")
    assert "gvisor" in runner_log.splitlines()[0], "the pod's kernel is the sentry's"
    assert '"object": "response"' in runner_log, "the runner reached the agent on the loopback"
    [agent] = pod["status"]["initContainerStatuses"]
    assert agent["name"] == AGENT_CONTAINER
    assert agent["state"]["terminated"]["exitCode"] == 0, "the agent was stopped by SIGTERM"
