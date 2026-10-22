"""S41-05: a hosted agent's container beside the runner (ADR 0080), as each executor starts it."""

from __future__ import annotations

from typing import Any

import pytest
from choregos_adapters.errors import ConfigurationError, UnsupportedClusterError, UpstreamError
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
    """`version`: the API server's `gitVersion`; `None` plays a `/version` that does not answer."""

    def __init__(self, version: str | None = "v1.30.2") -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.version = version

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs.get("json")))
        if path == "/version":
            if self.version is None:
                raise UpstreamError("kubernetes", "GET /version -> 503", status_code=503)
            return {"gitVersion": self.version}
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


@pytest.mark.parametrize("version", ["v1.28.15", "v1.27.3-eks-abc", "1.28+", "not-a-version"])
async def test_a_cluster_older_than_1_29_refuses_a_hosted_step_before_creating_anything(version: str) -> None:
    """S41-16: below 1.29 `restartPolicy: Always` is dropped, and the pod waits on its agent forever."""
    client = _RecordingClient(version)
    with pytest.raises(UnsupportedClusterError) as refused:
        await KubernetesJobExecutor(client=client).start(_spec())  # type: ignore[arg-type]
    assert refused.value.code == "hosted_unsupported_cluster"
    assert str(refused.value).startswith("hosted_unsupported_cluster:")
    assert [(method, path) for method, path, _ in client.calls] == [("GET", "/version")]


@pytest.mark.parametrize("version", ["v1.29.0", "v1.29.3-eks-abc", "1.30+", "v2.0.0"])
async def test_a_cluster_from_1_29_starts_the_hosted_step(version: str) -> None:
    client = _RecordingClient(version)
    await KubernetesJobExecutor(client=client).start(_spec())  # type: ignore[arg-type]
    assert any(method == "POST" and path.endswith("/jobs") for method, path, _ in client.calls)


async def test_the_version_is_read_once_per_executor() -> None:
    client = _RecordingClient("v1.29.3-eks-abc")
    executor = KubernetesJobExecutor(client=client)  # type: ignore[arg-type]
    await executor.start(_spec())
    await executor.start(_spec().model_copy(update={"run_id": "r-2"}))
    assert [path for _, path, _ in client.calls].count("/version") == 1


async def test_a_failed_read_is_retryable_and_not_kept() -> None:
    client = _RecordingClient(None)
    executor = KubernetesJobExecutor(client=client)  # type: ignore[arg-type]
    with pytest.raises(UpstreamError):
        await executor.start(_spec())
    client.version = "v1.31.0"
    await executor.start(_spec())
    assert [path for _, path, _ in client.calls].count("/version") == 2


async def test_a_step_without_a_hosted_agent_does_not_read_the_version() -> None:
    client = _RecordingClient("v1.25.0")
    await KubernetesJobExecutor(client=client).start(_spec(hosted=False))  # type: ignore[arg-type]
    assert all(path != "/version" for _, path, _ in client.calls), "a plain step runs on any cluster"


async def test_the_fake_refuses_as_the_job_executor_does() -> None:
    fake = FakeExecutor()
    await fake.start(_spec())
    fake.server_version = "v1.28.2"
    with pytest.raises(UnsupportedClusterError):
        await fake.start(_spec().model_copy(update={"run_id": "r-2"}))
    await fake.start(_spec(hosted=False).model_copy(update={"run_id": "r-3"}))
    assert [s.run_id for s in fake.started] == ["r-1", "r-3"]
