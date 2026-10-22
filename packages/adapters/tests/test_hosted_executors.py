"""S41-05: a hosted agent's container beside the runner (ADR 0080), as each executor starts it."""

from __future__ import annotations

from typing import Any

import pytest
from choregos_adapters.errors import ConfigurationError
from choregos_adapters.executor.aca import AcaExecutor
from choregos_adapters.executor.agent_sandbox import AgentSandboxExecutor
from choregos_adapters.executor.k8s_job import AGENT_CONTAINER, KubernetesJobExecutor
from choregos_adapters.executor.routing import RoutingExecutor
from choregos_adapters.executor.tekton import TektonExecutor
from choregos_adapters.fakes.executor import FakeExecutor
from choregos_contracts import ExecutorKind
from choregos_core.domain import AgentContainer, StageJobSpec

POOL = "harbor.example/choregos-runner:0.19.0"
IMAGE = "ghcr.io/acme/triager:1@sha256:" + "a" * 64


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs.get("json")))
        return None


def _spec(*, hosted: bool = True) -> StageJobSpec:
    agent = AgentContainer(
        image=IMAGE,
        env={
            "PORT": "8088",
            "OPENAI_BASE_URL": "http://127.0.0.1:4001/v1",
            "OPENAI_API_KEY": "choregos-loopback",
        },
        termination_grace_s=45,
        cpu="500m",
        memory="1Gi",
    )
    return StageJobSpec(
        run_id="r-1",
        project_slug="demo",
        namespace="choregos",
        runner_image=POOL,
        api_url="http://choregos-api:8000",
        run_token="run-token-secret",
        gateway_key="gateway-key-secret",
        remote_token="remote-token-secret",
        env={"HTTPS_PROXY": "http://choregos-egress:3128"},
        agent_container=agent if hosted else None,
    )


async def _pod(spec: StageJobSpec, **options: Any) -> dict[str, Any]:
    client = _RecordingClient()
    await KubernetesJobExecutor(client=client, **options).start(spec)  # type: ignore[arg-type]
    job = next(body for method, path, body in client.calls if method == "POST" and path.endswith("/jobs"))
    pod: dict[str, Any] = job["spec"]["template"]["spec"]
    return pod


async def test_the_agent_is_a_native_sidecar_that_starts_before_the_runner() -> None:
    pod = await _pod(_spec())
    [agent] = pod["initContainers"]
    assert agent["name"] == AGENT_CONTAINER and agent["image"] == IMAGE
    assert agent["restartPolicy"] == "Always", "a native sidecar: the Job ends with the runner"
    assert agent["ports"] == [{"name": "agent", "containerPort": 8088}]
    assert agent["resources"]["limits"] == {"cpu": "500m", "memory": "1Gi"}
    assert pod["terminationGracePeriodSeconds"] == 45
    assert [c["name"] for c in pod["containers"]] == ["runner"]


async def test_the_sidecar_holds_no_secret_of_the_run() -> None:
    """ADR 0080: the run's token, gateway key and remote token stay in the runner container."""
    pod = await _pod(_spec(), env_from_secrets=["git-identity"])
    [agent] = pod["initContainers"]
    assert "envFrom" not in agent and "volumeMounts" not in agent
    assert all("valueFrom" not in variable for variable in agent["env"])
    names = {variable["name"] for variable in agent["env"]}
    assert names == {"PORT", "OPENAI_BASE_URL", "OPENAI_API_KEY"}
    assert "secret" not in str(agent), "no value of the run's Secret reaches the agent"
    runner = pod["containers"][0]
    assert any("valueFrom" in variable for variable in runner["env"]), "the runner keeps its own"
    assert runner["envFrom"] == [{"secretRef": {"name": "git-identity"}}]
    assert pod["enableServiceLinks"] is False


async def test_the_sidecar_runs_unprivileged_like_the_runner() -> None:
    pod = await _pod(_spec())
    context = pod["initContainers"][0]["securityContext"]
    assert context["allowPrivilegeEscalation"] is False
    assert context["capabilities"] == {"drop": ["ALL"]}
    assert context["runAsNonRoot"] is True
    assert context["seccompProfile"] == {"type": "RuntimeDefault"}


async def test_a_step_without_a_hosted_agent_keeps_a_single_container() -> None:
    pod = await _pod(_spec(hosted=False))
    assert "initContainers" not in pod and "terminationGracePeriodSeconds" not in pod


class _Sandbox(FakeExecutor):
    kind = ExecutorKind.AGENT_SANDBOX


class _Jobs(FakeExecutor):
    kind = ExecutorKind.K8S_JOB


async def test_a_hosted_step_in_the_pool_image_still_goes_to_jobs() -> None:
    sandbox, jobs = _Sandbox(), _Jobs()
    routing = RoutingExecutor(sandbox, jobs, POOL)
    ref = await routing.start(_spec())
    plain = await routing.start(_spec(hosted=False).model_copy(update={"run_id": "r-2"}))
    assert [s.run_id for s in jobs.started] == ["r-1"] and str(ref.kind) == "k8s_job"
    assert jobs.started[0].agent_container is not None
    assert [s.run_id for s in sandbox.started] == ["r-2"] and str(plain.kind) == "agent_sandbox"


@pytest.mark.parametrize(
    ("make", "named"),
    [
        (lambda c: AgentSandboxExecutor(client=c, pool_image=POOL), "warm-pool"),
        (TektonExecutor, "PipelineRun"),
        (lambda c: AcaExecutor(c, environment_id="/env"), "Container Apps"),
    ],
)
async def test_an_executor_that_cannot_hold_a_sidecar_refuses_before_creating_anything(
    make: Any, named: str
) -> None:
    client = _RecordingClient()
    with pytest.raises(ConfigurationError, match="hosted agent") as refused:
        await make(client).start(_spec())
    assert named in str(refused.value)
    assert client.calls == [], "nothing is created for a step that cannot run"
