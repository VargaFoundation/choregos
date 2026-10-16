"""S40-03c: `start_run` reports what the executor that took THIS run can do (activity code, no workflow change)."""

from __future__ import annotations

from choregos_adapters.executor.routing import RoutingExecutor
from choregos_adapters.fakes.executor import FakeExecutor
from choregos_contracts import ExecutorKind
from choregos_core import ExecRef
from choregos_orchestrator.activities.execution import _capabilities_for


class _Sandbox(FakeExecutor):
    kind = ExecutorKind.AGENT_SANDBOX


class _Jobs(FakeExecutor):
    kind = ExecutorKind.K8S_JOB


def test_a_run_on_the_second_executor_reports_its_lesser_capabilities() -> None:
    routing = RoutingExecutor(_Sandbox(capabilities=frozenset({"suspend", "resume"})), _Jobs(), "pool")
    sandbox_ref = ExecRef(kind=ExecutorKind.AGENT_SANDBOX, name="a", run_id="r1")
    jobs_ref = ExecRef(kind=ExecutorKind.K8S_JOB, name="b", run_id="r2")
    assert _capabilities_for(routing, sandbox_ref, ["x"]) == ["resume", "suspend"]
    assert _capabilities_for(routing, jobs_ref, ["x"]) == []


def test_an_executor_with_one_kind_keeps_its_own() -> None:
    ref = ExecRef(kind=ExecutorKind.K8S_JOB, name="b", run_id="r2")
    assert _capabilities_for(_Jobs(), ref, ["queue"]) == ["queue"]
