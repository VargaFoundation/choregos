"""S40-03c: a sandbox deployment runs a step with its own image on a second executor (ADR 0072)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from choregos_adapters.executor.routing import RoutingExecutor
from choregos_adapters.fakes.executor import FakeExecutor
from choregos_adapters.registry import with_custom_image_executor
from choregos_contracts import ExecutorKind
from choregos_core.domain import ExecStatus, StageJobSpec

POOL = "registry.example/choregos-runner:1.0"


class _Sandbox(FakeExecutor):
    kind = ExecutorKind.AGENT_SANDBOX


class _Jobs(FakeExecutor):
    kind = ExecutorKind.K8S_JOB


def _spec(run_id: str, image: str) -> StageJobSpec:
    return StageJobSpec(
        run_id=run_id,
        project_slug="demo",
        namespace="choregos",
        runner_image=image,
        api_url="http://choregos-api:8000",
        run_token="token",
    )


def _routing() -> tuple[RoutingExecutor, _Sandbox, _Jobs]:
    sandbox = _Sandbox(capabilities=frozenset({"queue", "suspend", "resume", "renew"}))
    jobs = _Jobs(capabilities=frozenset({"queue"}))
    return RoutingExecutor(sandbox, jobs, POOL), sandbox, jobs


async def test_a_pool_image_step_goes_to_the_sandbox_and_a_custom_image_to_jobs() -> None:
    routing, sandbox, jobs = _routing()
    pool_ref = await routing.start(_spec("r-pool", POOL))
    default_ref = await routing.start(_spec("r-default", POOL))
    custom_ref = await routing.start(_spec("r-custom", "registry.example/agent-tools:3"))
    assert [s.run_id for s in sandbox.started] == ["r-pool", "r-default"]
    assert [s.run_id for s in jobs.started] == ["r-custom"]
    assert str(pool_ref.kind) == "agent_sandbox" and str(custom_ref.kind) == "k8s_job"
    assert str(default_ref.kind) == "agent_sandbox"


async def test_what_follows_start_reaches_the_executor_that_started_the_run() -> None:
    routing, sandbox, jobs = _routing()
    custom_ref = await routing.start(_spec("r-custom", "registry.example/agent-tools:3"))
    pool_ref = await routing.start(_spec("r-pool", POOL))
    jobs.statuses["r-custom"] = ExecStatus(state="succeeded")
    sandbox.statuses["r-pool"] = ExecStatus(state="running")
    assert (await routing.status(custom_ref)).state == "succeeded"
    assert (await routing.status(pool_ref)).state == "running"
    assert [line async for line in routing.logs(custom_ref)] == jobs.log_lines
    await routing.cancel(custom_ref)
    assert jobs.cancelled and not sandbox.cancelled


async def test_capabilities_are_the_delegate_s() -> None:
    routing, _, _ = _routing()
    custom_ref = await routing.start(_spec("r-custom", "registry.example/agent-tools:3"))
    pool_ref = await routing.start(_spec("r-pool", POOL))
    assert routing.capabilities == {"queue", "suspend", "resume", "renew"}
    assert routing.capabilities_for(pool_ref) == {"queue", "suspend", "resume", "renew"}
    assert routing.capabilities_for(custom_ref) == {"queue"}


async def test_a_run_on_the_secondary_is_never_suspended() -> None:
    routing, sandbox, jobs = _routing()
    jobs_ref = await routing.start(_spec("r-custom", "registry.example/agent-tools:3"))
    sandbox_ref = await routing.start(_spec("r-pool", POOL))
    until = datetime(2026, 10, 17, tzinfo=UTC)
    assert await routing.suspend(sandbox_ref, until) is True
    assert sandbox.suspended and not jobs.suspended
    jobs.suspend = None  # type: ignore[assignment,method-assign]  # a k8s_job has no `suspend`
    assert await routing.suspend(jobs_ref, until) is False


def test_the_wiring_wraps_only_a_sandbox_with_a_known_pool_and_a_named_second_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = _Sandbox()
    monkeypatch.delenv("CHOREGOS_CUSTOM_IMAGE_EXECUTOR", raising=False)
    monkeypatch.setenv("CHOREGOS_RUNNER_IMAGE", POOL)
    assert with_custom_image_executor(sandbox) is sandbox, "no second executor named: the refusal stays"
    monkeypatch.setenv("CHOREGOS_CUSTOM_IMAGE_EXECUTOR", "k8s_job")
    monkeypatch.setenv("CHOREGOS_FAKES", "1")  # `build` then answers with a fake: no cluster needed here
    wrapped = with_custom_image_executor(sandbox)
    assert isinstance(wrapped, RoutingExecutor) and wrapped.pool_image == POOL
    assert isinstance(wrapped.secondary, FakeExecutor)
    jobs = _Jobs()
    assert with_custom_image_executor(jobs) is jobs, "only agent_sandbox is wrapped"
    monkeypatch.delenv("CHOREGOS_RUNNER_IMAGE")
    assert with_custom_image_executor(sandbox) is sandbox, "an unknown pool image: an override cannot be told"
