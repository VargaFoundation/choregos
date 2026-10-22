"""S41-16 — a hosted agent's container as a native sidecar, on a real API server.

What only a cluster shows: the API server keeps `restartPolicy: Always` on the init container
(1.29 or later), the kubelet starts the agent before the runner, the Job completes when the runner
exits although the agent never would on its own, and the agent is stopped by SIGTERM, not killed
at the end of its grace period. Below 1.29 the executor refuses the start and creates nothing.

The real `KubernetesJobExecutor` creates the Job, through `kubectl proxy`, with the reference
hosted example as the agent. The runner container is a probe that plays the runner's part of a
hosted step: it waits for the agent's `/readiness` on the pod's loopback, sends it a `/responses`
request and exits 0. The reference server exits 0 on SIGTERM (`examples/hosted/reference`); a
default Python process ends on 143 and a SIGKILL on 137, so the agent's exit code says which.

It needs the reference image, built and loadable by the cluster (`docker build -t
reference-agent:dev examples/hosted/reference && kind load docker-image reference-agent:dev
--name choregos`), named by `CHOREGOS_HOSTED_REFERENCE_IMAGE`; without it, it is skipped.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import subprocess
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from choregos_adapters.errors import UnsupportedClusterError
from choregos_adapters.executor.k8s_job import AGENT_CONTAINER, KubernetesJobExecutor
from choregos_adapters.executor.tekton import KubernetesClient
from choregos_core.domain import AgentContainer, StageJobSpec
from choregos_core.hosted import NATIVE_SIDECARS_MIN, kubernetes_version

from .conftest import context, kubectl_shared

pytestmark = pytest.mark.cluster

PROBE = "curlimages/curl:8.11.1"
#: The runner's part of a hosted step, without the platform: the agent answers on the loopback.
RUNNER = (
    "for i in $(seq 1 90); do curl -sf http://127.0.0.1:8088/readiness && break; sleep 1; done; "
    "curl -sf -X POST -H 'content-type: application/json' -d '{\"input\": \"hello\"}' "
    "http://127.0.0.1:8088/responses"
)


def _reference_image() -> str:
    image = os.environ.get("CHOREGOS_HOSTED_REFERENCE_IMAGE", "")
    if not image:
        pytest.skip("CHOREGOS_HOSTED_REFERENCE_IMAGE unset: the reference hosted example's image is needed")
    return image


@pytest.fixture(scope="module")
def ns() -> Iterator[str]:
    name = f"choregos-hosted-{secrets.token_hex(3)}"
    kubectl_shared("create", "namespace", name)
    try:
        kubectl_shared("create", "serviceaccount", "choregos-runner", "-n", name)
        yield name
    finally:
        kubectl_shared("delete", "namespace", name, "--wait=false", "--ignore-not-found", check=False)


@pytest.fixture
async def executor() -> AsyncIterator[KubernetesJobExecutor]:
    """The real executor, against the API server through `kubectl proxy` (it authenticates)."""
    proxy = subprocess.Popen(
        ["kubectl", "--context", context(non_destructive=True), "proxy", "--port=0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert proxy.stdout is not None
        # "Starting to serve on 127.0.0.1:41373"
        address = proxy.stdout.readline().strip().rsplit(" ", 1)[-1]
        client = KubernetesClient(f"http://{address}", token=None, verify=False)
        try:
            yield KubernetesJobExecutor(client=client, ttl_seconds=600)
        finally:
            await client.aclose()
    finally:
        proxy.terminate()
        proxy.wait(timeout=10)


def _spec(ns: str, image: str) -> StageJobSpec:
    agent = AgentContainer(
        image=image,
        env={"PORT": "8088", "CHOREGOS_RUN_ID": "hosted-1", "CHOREGOS_MODEL": "reference"},
        termination_grace_s=30,
        cpu="250m",
        memory="256Mi",
    )
    return StageJobSpec(
        run_id=f"hosted-{secrets.token_hex(3)}",
        project_slug="demo",
        namespace=ns,
        runner_image=PROBE,
        api_url="http://192.0.2.1",
        run_token="t",
        timeout_minutes=5,
        cpu="100m",
        memory="128Mi",
        agent_container=agent,
        runner_command=["sh", "-c", RUNNER],
    )


def _pod(ns: str, run_id: str) -> dict[str, Any]:
    pods = json.loads(
        kubectl_shared("-n", ns, "get", "pods", "-l", f"choregos/run-id={run_id}", "-o", "json")
    )
    (pod,) = pods["items"]
    return dict(pod)


async def _cluster_version(executor: KubernetesJobExecutor) -> tuple[int, int]:
    parsed = kubernetes_version(await executor.server_version())
    assert parsed is not None, "the API server's /version could not be read"
    return parsed


async def test_the_run_finishes_and_the_agent_is_stopped_by_sigterm(
    ns: str, executor: KubernetesJobExecutor
) -> None:
    image = _reference_image()
    if await _cluster_version(executor) < NATIVE_SIDECARS_MIN:
        pytest.skip("the cluster is older than 1.29: the refusal is the other test's")
    spec = _spec(ns, image)
    ref = await executor.start(spec)
    state, deadline = "pending", time.monotonic() + 300
    while time.monotonic() < deadline:
        state = (await executor.status(ref)).state
        if state in {"succeeded", "failed"}:
            break
        await asyncio.sleep(3)
    assert state == "succeeded", f"the hosted run did not finish: {state}"

    pod = _pod(ns, spec.run_id)
    [declared] = pod["spec"]["initContainers"]
    assert declared["restartPolicy"] == "Always", "the API server dropped the native sidecar"
    runner_log = kubectl_shared("-n", ns, "logs", pod["metadata"]["name"], "-c", "runner")
    assert '"object": "response"' in runner_log, "the runner reached the agent on the loopback"
    [agent] = pod["status"]["initContainerStatuses"]
    assert agent["name"] == AGENT_CONTAINER
    stopped = agent["state"]["terminated"]
    assert stopped["exitCode"] == 0, f"the agent was not stopped by SIGTERM: {stopped}"


async def test_below_1_29_the_start_is_refused_and_nothing_is_created(
    ns: str, executor: KubernetesJobExecutor
) -> None:
    if await _cluster_version(executor) >= NATIVE_SIDECARS_MIN:
        pytest.skip("the cluster runs 1.29 or later: only an older one refuses")
    spec = _spec(ns, "registry.invalid/agent@sha256:" + "a" * 64)
    with pytest.raises(UnsupportedClusterError):
        await executor.start(spec)
    jobs = json.loads(kubectl_shared("-n", ns, "get", "jobs", "-o", "json"))
    assert jobs["items"] == [], "nothing is created on a cluster that cannot run the sidecar"
