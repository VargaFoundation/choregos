"""Every executor hands the runner its gateway key through the run's Secret, next to its token (#523).

The key used to reach the runner through `GET /internal/runs/{id}/input`, i.e. from `runs.stage_input`
— so it sat in the database for the run's lifetime. Now `StageJobSpec.gateway_key` goes to the run's
Secret (k8s_job, tekton, agent_sandbox), a job secret (aca) or the container's environment
(local_docker), and the runner reads `CHOREGOS_RUN_GATEWAY_KEY`. Proved here without a cluster.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

import httpx
from choregos_adapters.executor.aca import AcaExecutor, AzureArmClient
from choregos_adapters.executor.agent_sandbox import AgentSandboxExecutor
from choregos_adapters.executor.k8s_job import KubernetesJobExecutor
from choregos_adapters.executor.local_docker import LocalDockerExecutor
from choregos_adapters.executor.tekton import CORE_API, TEKTON_API, KubernetesClient, TektonExecutor
from choregos_adapters.fakes import FakeExecutor
from choregos_adapters.fakes.agent_sandbox import FakeSandboxCluster
from choregos_contracts import ExecutorKind, StageInput, StageResult, StageStatus
from choregos_core.domain import RUN_GATEWAY_KEY_ENV, ExecRef, StageJobSpec

from ._transport import Fil

KEY = "sk-run-gateway-key"
NS = "choregos"


def _spec(run_id: str = "r-1", **extra: Any) -> StageJobSpec:
    return StageJobSpec(
        run_id=run_id,
        project_slug="demo",
        namespace=NS,
        runner_image="runner:dev",
        api_url="http://choregos-api:8000/api/v1/internal",
        run_token="run-token",
        gateway_key=KEY,
        **extra,
    )


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


# ─────────────────────────────── k8s_job ───────────────────────────────


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs.get("json")))
        return None


async def test_k8s_job_puts_the_key_in_the_run_secret_and_the_pod_reads_it_by_reference() -> None:
    client = _Recorder()
    await KubernetesJobExecutor(client=client).start(_spec())  # type: ignore[arg-type]

    secret = next(body for m, p, body in client.calls if m == "POST" and p.endswith("/secrets"))
    assert secret["data"]["gateway-key"] == _b64(KEY)
    job = next(body for m, p, body in client.calls if m == "POST" and p.endswith("/jobs"))
    env = {e["name"]: e for e in job["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env[RUN_GATEWAY_KEY_ENV]["valueFrom"]["secretKeyRef"] == {
        "name": "run-r-1",
        "key": "gateway-key",
        "optional": True,
    }
    assert KEY not in json.dumps(job), "the key is read by reference, never written in the Job"


async def test_k8s_job_renew_replaces_the_token_and_keeps_the_key() -> None:
    client = _Recorder()
    executor = KubernetesJobExecutor(client=client)  # type: ignore[arg-type]
    ref = ExecRef(kind=ExecutorKind.K8S_JOB, name="run-r-1", namespace=NS, run_id="r-1")
    await executor.renew(ref, "fresh-token")

    writes = [(m, body) for m, p, body in client.calls if p.endswith("/secrets/run-r-1")]
    assert writes == [("PATCH", {"data": {"token": _b64("fresh-token")}})], (
        "a merge patch of the token alone: a PUT would drop the key"
    )


# ─────────────────────────────── tekton ───────────────────────────────


async def test_tekton_puts_the_key_in_the_run_secret() -> None:
    fil = Fil(
        {
            ("POST", f"{CORE_API}/namespaces/{NS}/secrets"): (201, {}),
            ("POST", f"{TEKTON_API}/namespaces/{NS}/pipelineruns"): (201, {}),
        }
    )
    client = KubernetesClient("https://k8s.test", token="sa", client=fil.client())
    await TektonExecutor(client).start(_spec())
    secret = fil.corps(-2)
    assert secret["data"]["gateway-key"] == _b64(KEY)
    assert KEY not in json.dumps(fil.corps(-1)), "never in the PipelineRun"


async def test_tekton_rewrites_a_secret_left_by_an_earlier_attempt() -> None:
    """A run started again must not run with the key of an attempt rolled back (revoked or unbooked)."""
    path = f"{CORE_API}/namespaces/{NS}/secrets/run-r-1"
    fil = Fil(
        {
            ("GET", path): (200, {"data": {"gateway-key": _b64("sk-old")}}),
            ("PUT", path): (200, {}),
            ("POST", f"{TEKTON_API}/namespaces/{NS}/pipelineruns"): (201, {}),
        }
    )
    client = KubernetesClient("https://k8s.test", token="sa", client=fil.client())
    await TektonExecutor(client).start(_spec())
    put = next(r for r in fil.requetes if r.method == "PUT")
    assert json.loads(put.content)["data"]["gateway-key"] == _b64(KEY)


# ─────────────────────────────── agent_sandbox ───────────────────────────────


def _sandbox_executor(cluster: FakeSandboxCluster) -> AgentSandboxExecutor:
    return AgentSandboxExecutor(
        client=cluster,  # type: ignore[arg-type]
        http=httpx.AsyncClient(transport=cluster.transport),
    )


def _sandbox_ref(run_id: str = "r-1") -> ExecRef:
    return ExecRef(kind=ExecutorKind.AGENT_SANDBOX, name=f"run-{run_id}", namespace=NS, run_id=run_id)


async def test_agent_sandbox_seals_the_key_with_the_token_to_the_run_pod_only() -> None:
    cluster = FakeSandboxCluster()
    cluster.warm(1)
    executor = _sandbox_executor(cluster)
    ref = await executor.start(_spec())
    cluster.reconcile()
    assert (await executor.status(ref)).state == "running"

    pod = cluster.pod_of("r-1")
    assert pod is not None
    assert pod.assignments[0]["env"][RUN_GATEWAY_KEY_ENV] == KEY
    for body in pod.wire:
        assert KEY.encode() not in body and _b64(KEY).encode() not in body, "sealed on the wire"
    assert KEY not in json.dumps(cluster.claims), "never in the claim"


async def test_agent_sandbox_resume_gives_the_continuation_its_fresh_key() -> None:
    cluster = FakeSandboxCluster()
    cluster.warm(1)
    executor = _sandbox_executor(cluster)
    ref = await executor.start(_spec())
    cluster.reconcile()
    await executor.status(ref)
    cluster.finish("r-1")
    await executor.suspend(ref, datetime(2026, 10, 14, tzinfo=UTC))

    resumed = await executor.resume(
        ref, "second-token", datetime(2026, 10, 14, 1, tzinfo=UTC), gateway_key="sk-continuation"
    )
    assert resumed is True
    cluster.reconcile()
    assert (await executor.status(ref)).state == "running"
    sandbox = cluster.sandbox_of("r-1")
    assert sandbox is not None
    first_pod, second_pod = sandbox.pods
    assert first_pod.assignments[0]["env"][RUN_GATEWAY_KEY_ENV] == KEY
    assert second_pod.assignments[0]["env"][RUN_GATEWAY_KEY_ENV] == "sk-continuation"


async def test_agent_sandbox_renew_without_a_key_keeps_the_key() -> None:
    cluster = FakeSandboxCluster()
    executor = _sandbox_executor(cluster)
    ref = await executor.start(_spec())
    await executor.renew(ref, "fresh-token")
    cluster.reconcile()
    await executor.status(ref)
    pod = cluster.pod_of("r-1")
    assert pod is not None
    assert pod.assignments[0]["run_token"] == "fresh-token"
    assert pod.assignments[0]["env"][RUN_GATEWAY_KEY_ENV] == KEY


# ─────────────────────────────── aca ───────────────────────────────


async def test_aca_passes_the_key_as_a_job_secret() -> None:
    definition: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/executions"):
            return httpx.Response(200, json={"value": []})
        if request.method == "GET":
            return httpx.Response(404, json={})
        if request.method == "PUT":
            definition.update(json.loads(request.content))
        return httpx.Response(200, json={})

    client = AzureArmClient(
        subscription_id="sub",
        resource_group="rg",
        token="arm",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    await AcaExecutor(client, environment_id="/env").start(_spec())

    container = definition["properties"]["template"]["containers"][0]
    assert {"name": RUN_GATEWAY_KEY_ENV, "secretRef": "run-gateway-key"} in container["env"]
    assert KEY not in json.dumps(container), "the rendered definition never shows the key"
    secrets = definition["properties"]["configuration"]["secrets"]
    assert {"name": "run-gateway-key", "value": KEY} in secrets


# ─────────────────────────────── local_docker ───────────────────────────────


async def test_local_docker_gives_the_container_the_key() -> None:
    calls: list[list[str]] = []

    envs_given: list[dict[str, str] | None] = []

    async def docker(
        *args: str, timeout: float = 120.0, env: dict[str, str] | None = None
    ) -> tuple[int, str, str]:
        calls.append(list(args))
        envs_given.append(env)
        return (1, "", "no such container") if args[0] == "inspect" else (0, "", "")

    executor = LocalDockerExecutor(network="net")
    executor._run = docker  # type: ignore[method-assign]
    await executor.start(_spec())
    run = next(call for call in calls if call[0] == "run")
    envs = [run[i + 1] for i, arg in enumerate(run) if arg == "--env"]
    # Named on the command line, valued in docker's own environment (#439).
    assert RUN_GATEWAY_KEY_ENV in envs and KEY not in " ".join(run)
    assert (envs_given[calls.index(run)] or {})[RUN_GATEWAY_KEY_ENV] == KEY


# ─────────────────────────────── the fake ───────────────────────────────


async def test_the_fake_executor_hands_its_handler_the_key_a_runner_would_read() -> None:
    seen: list[str | None] = []

    def handler(stage_input: StageInput) -> StageResult:
        seen.append(stage_input.gateway_key)
        return StageResult(status=StageStatus.DONE, summary="ok")

    stage_input = _stage_input()
    executor = FakeExecutor(handler=handler)
    await executor.start(_spec(stage_input=stage_input))
    assert seen == [KEY]


def _stage_input() -> StageInput:
    return StageInput.model_validate(
        {
            "run_id": "r-1",
            "attempt": 1,
            "project": {"slug": "demo", "org": "varga"},
            "work_item": {"key": "DEMO-1", "title": "t", "url": "http://t"},
            "transition": {"id": "t", "role": "implement", "from": "a", "to": "b"},
            "agent": {"backend": "claude-code"},
            "model": {"litellm_model": "platform/standard", "base_url": "http://gw"},
            "budget": {"usd": 1.0, "max_turns": 10, "max_minutes": 10},
            "playbook": {"ref": "p@sha256:0", "prompt": "do"},
            "callbacks": {"api_url": "http://api", "run_token": "[REDACTED]"},
        }
    )
