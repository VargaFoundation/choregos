"""ADR 0146 §9 (S65-04d): under `container`, an executor that cannot start the agent's own container
refuses the run at `start`, naming itself, before anything reaches its backend; routing never sends such
a run to an executor that cannot; and what the API judges is what the executor declares."""

from __future__ import annotations

from typing import Any

import pytest
from tranibor_adapters import available, build
from tranibor_adapters.errors import AgentIsolationRefusedError, ConfigurationError
from tranibor_adapters.executor.aca import AcaExecutor
from tranibor_adapters.executor.agent_sandbox import AgentSandboxExecutor
from tranibor_adapters.executor.isolation import declared_capabilities
from tranibor_adapters.executor.k8s_job import KubernetesJobExecutor
from tranibor_adapters.executor.local_docker import LocalDockerExecutor
from tranibor_adapters.executor.routing import RoutingExecutor
from tranibor_adapters.executor.tekton import TektonExecutor
from tranibor_adapters.fakes.executor import FakeExecutor
from tranibor_contracts import ExecutorKind
from tranibor_core.agent_isolation import (
    AGENT_ISOLATION_UNAVAILABLE,
    SEPARATE_AGENT,
    agent_isolation_refusal,
    deployment_agent_isolation,
)
from tranibor_core.domain import StageJobSpec

POOL = "registry.example/choregos-runner:1.0"


class _Recording:
    """A cluster API (or an ARM one) that records every call: a refusal must make none."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.scope = "/subscriptions/s/resourceGroups/g"
        self.resource_group = "g"

    async def request(self, method: str, path: str, **_: Any) -> Any:
        self.calls.append((method, path))
        return None


class _Docker(LocalDockerExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, ...]] = []

    async def _run_raw(
        self, *args: str, timeout: float = 120.0, env: dict[str, str] | None = None
    ) -> tuple[int, bytes, str]:
        self.calls.append(args)
        return 1, b"", ""


def _spec(isolation: str = "container", image: str = POOL, run_id: str = "r-1") -> StageJobSpec:
    return StageJobSpec(
        run_id=run_id,
        project_slug="demo",
        namespace="choregos",
        runner_image=image,
        api_url="http://choregos-api:8000",
        run_token="token",
        agent_isolation=isolation,  # type: ignore[arg-type]
    )


def _without(kind: str) -> tuple[Any, Any]:
    """An executor of `kind` as built today, and what records what it sent."""
    if kind == "local_docker":
        docker = _Docker()
        return docker, docker
    client = _Recording()
    executors: dict[str, Any] = {
        "k8s_job": lambda: KubernetesJobExecutor(client=client),  # type: ignore[arg-type]
        "agent_sandbox": lambda: AgentSandboxExecutor(client=client),  # type: ignore[arg-type]
        "tekton": lambda: TektonExecutor(client=client),  # type: ignore[arg-type]
        "aca": lambda: AcaExecutor(client, environment_id="env"),  # type: ignore[arg-type]
    }
    return executors[kind](), client


def _sent(recorder: Any) -> list[Any]:
    return list(recorder.calls)


@pytest.mark.parametrize("kind", ["k8s_job", "agent_sandbox", "local_docker", "tekton", "aca"])
async def test_an_executor_that_cannot_isolate_refuses_before_sending_anything(kind: str) -> None:
    executor, recorder = _without(kind)
    if SEPARATE_AGENT in executor.capabilities:
        pytest.skip(f"{kind} starts the agent's container: its own tests prove the shape")
    with pytest.raises(AgentIsolationRefusedError) as refused:
        await executor.start(_spec("container"))
    assert f"`{kind}`" in str(refused.value), "the refusal names the executor"
    assert isinstance(refused.value, ConfigurationError), "not retried by the activity"
    assert refused.value.code == AGENT_ISOLATION_UNAVAILABLE
    assert _sent(recorder) == [], "nothing reaches the backend"


async def test_the_same_executor_runs_the_step_under_process() -> None:
    executor, recorder = _without("k8s_job")
    await executor.start(_spec("process"))
    assert any(method == "POST" for method, _ in _sent(recorder))


def test_a_warm_pool_separates_the_agent_only_when_its_template_has_the_container() -> None:
    assert SEPARATE_AGENT not in AgentSandboxExecutor(client=_Recording()).capabilities  # type: ignore[arg-type]
    container = AgentSandboxExecutor(client=_Recording(), agent_isolation="container")  # type: ignore[arg-type]
    assert SEPARATE_AGENT in container.capabilities
    assert {"queue", "renew", "snapshot", "suspend", "resume"} <= container.capabilities


async def test_a_warm_pool_made_with_the_agents_container_takes_a_container_run() -> None:
    client = _Recording()
    executor = AgentSandboxExecutor(client=client, agent_isolation="container")  # type: ignore[arg-type]
    await executor.start(_spec("container"))
    assert any(method == "POST" and path.endswith("/sandboxclaims") for method, path in client.calls)


# What the API judges is what `start` does ──────────────────────────────────────────────

KUBE = {"kubernetes": {"base_url": "https://example.invalid", "token": "outside", "verify": False}}
CONFIGS = {
    "k8s_job": KUBE,
    "tekton": KUBE,
    "agent_sandbox": KUBE,
    "local_docker": {},
    "aca": {"subscription_id": "s", "resource_group": "g", "environment_id": "e"},
}


@pytest.mark.parametrize("isolation", ["process", "container"])
@pytest.mark.parametrize("kind", sorted(CONFIGS))
def test_the_api_reads_what_the_built_executor_declares(
    kind: str, isolation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRANIBOR_RUNNER_AGENT_ISOLATION", isolation)
    # The real executors, not the fakes another test may have switched on.
    monkeypatch.delenv("TRANIBOR_FAKES", raising=False)
    built = build("runtime", kind, CONFIGS[kind])
    assert declared_capabilities(kind, agent_isolation=isolation) == built.capabilities


def test_every_shipped_executor_is_judged() -> None:
    """A new executor kind must say what it declares, or `/readiness` reads nothing for it."""
    assert set(available("runtime")) - {"fake"} == set(CONFIGS)


def test_one_sentence_for_the_executor_and_the_readiness() -> None:
    assert agent_isolation_refusal("process", "aca", frozenset()) is None
    assert agent_isolation_refusal("container", "k8s_job", {SEPARATE_AGENT}) is None
    message = agent_isolation_refusal("container", "aca", frozenset({"queue"}))
    assert message is not None
    for wanted in ("`aca`", "separate_agent", "global.runner.agentIsolation", "ADR 0146"):
        assert wanted in message


def test_the_deployment_setting_refuses_a_word_it_does_not_know() -> None:
    assert deployment_agent_isolation({}) == "process"
    assert deployment_agent_isolation({"TRANIBOR_RUNNER_AGENT_ISOLATION": "container"}) == "container"
    with pytest.raises(ValueError, match="TRANIBOR_RUNNER_AGENT_ISOLATION"):
        deployment_agent_isolation({"TRANIBOR_RUNNER_AGENT_ISOLATION": "containers"})


# Routing ──────────────────────────────────────────────────────────────────────────────────


class _Sandbox(FakeExecutor):
    kind = ExecutorKind.AGENT_SANDBOX


class _Jobs(FakeExecutor):
    kind = ExecutorKind.K8S_JOB


def _routing(sandbox: frozenset[str], jobs: frozenset[str]) -> tuple[RoutingExecutor, _Sandbox, _Jobs]:
    primary, secondary = _Sandbox(capabilities=sandbox), _Jobs(capabilities=jobs)
    return RoutingExecutor(primary, secondary, POOL), primary, secondary


async def test_a_container_run_goes_to_the_executor_that_can_isolate_it() -> None:
    routing, sandbox, jobs = _routing(frozenset({"queue"}), frozenset({"queue", SEPARATE_AGENT}))
    ref = await routing.start(_spec("container"))
    assert str(ref.kind) == "k8s_job"
    assert [s.run_id for s in jobs.started] == ["r-1"] and sandbox.started == []
    # Under `process` the pool keeps its runs.
    await routing.start(_spec("process", run_id="r-2"))
    assert [s.run_id for s in sandbox.started] == ["r-2"]


async def test_the_pool_keeps_a_container_run_it_can_isolate() -> None:
    routing, sandbox, jobs = _routing(frozenset({SEPARATE_AGENT}), frozenset({SEPARATE_AGENT}))
    await routing.start(_spec("container"))
    assert [s.run_id for s in sandbox.started] == ["r-1"] and jobs.started == []


async def test_when_neither_can_the_routing_refuses_naming_both() -> None:
    routing, sandbox, jobs = _routing(frozenset({"queue"}), frozenset({"queue"}))
    with pytest.raises(AgentIsolationRefusedError) as refused:
        await routing.start(_spec("container"))
    assert "`agent_sandbox`" in str(refused.value) and "`k8s_job`" in str(refused.value)
    assert sandbox.start_calls_by_run == {} and jobs.start_calls_by_run == {}


async def test_a_custom_image_step_is_never_sent_back_to_the_pool_for_its_isolation() -> None:
    """The pool cannot honour another image: the second executor refuses it, the pool never runs it."""
    routing, sandbox, _jobs = _routing(frozenset({SEPARATE_AGENT}), frozenset({"queue"}))
    with pytest.raises(AgentIsolationRefusedError, match="`k8s_job`"):
        await routing.start(_spec("container", image="registry.example/agent-tools:3"))
    assert sandbox.start_calls_by_run == {}
