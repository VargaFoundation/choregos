"""A run that waits for a slot must not call itself "running".

The concurrency ceiling creates the Job SUSPENDED: it has no pod. Marking it
"running" would make the board, the run page and the measures lie — nothing runs.
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_contracts import ExecutorKind
from choregos_core.domain import ExecRef, ExecStatus, StageJobSpec

from .conftest import Fixture

pytestmark = pytest.mark.asyncio


class _QueueingExecutor:
    """An executor that queues, then admits. Like `k8s_job`, without a cluster."""

    kind = ExecutorKind.K8S_JOB
    capabilities = frozenset({"queue"})

    def __init__(self) -> None:
        self.admitted = False

    async def start(self, spec: StageJobSpec) -> ExecRef:
        return ExecRef(
            kind=self.kind, name=f"run-{spec.run_id}", namespace=spec.namespace, run_id=spec.run_id
        )

    async def status(self, ref: ExecRef) -> ExecStatus:
        if self.admitted:
            return ExecStatus(state="running")
        return ExecStatus(state="pending", message="waiting for a slot (ceiling 2 per namespace)")

    async def cancel(self, ref: ExecRef) -> None:
        return None

    def logs(self, ref: ExecRef) -> Any:
        raise NotImplementedError


async def _run_prepare(setup: Fixture) -> dict[str, Any]:
    from choregos_orchestrator.activities import stage as activities

    return await activities.prepare_stage(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "transition_id": "t-implement",
            "role": "implement",
            "actor": "dev",
            "from_state": "ready",
            "to_state": "in_progress",
            "attempt": 1,
        }
    )


async def test_a_queued_run_is_not_called_running(setup: Fixture) -> None:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities import stage as activities

    executor = _QueueingExecutor()
    setup.adapters.executor = executor
    plan = await _run_prepare(setup)
    run_id = str(plan["run_id"])

    started = await activities.start_run(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "run_id": run_id,
            "stage_input": plan["stage_input"],
        }
    )
    assert started["queued"] is True

    async with session_scope() as session:
        run = await session.get(Run, run_id)
        assert run is not None
        assert run.status == "queued", "no pod is running: say so"

    # The slot frees up: the next status poll must see it. The zero budget then makes the
    # wait expire — and since 2026-09-25 an expired run is cleaned up; what proves the
    # admission is the start date and the `run.started` event.
    executor.admitted = True
    await activities.await_run(
        {"project_id": setup.project_id, "run_id": run_id, "timeout_minutes": 0, "poll_seconds": 0.01}
    )
    async with session_scope() as session:
        run = await session.get(Run, run_id)
        assert run is not None and run.started_at is not None, "admitted, so started"
        from choregos_api.db.models import Event
        from sqlalchemy import select

        types = (
            (await session.execute(select(Event.type).where(Event.work_item_id == setup.work_item_id)))
            .scalars()
            .all()
        )
        assert any(t.endswith("run.started") for t in types), types


async def test_preparing_the_same_stage_twice_does_not_break(setup: Fixture) -> None:
    """A Temporal activity may go over the SAME run again, and the gateway key is
    deterministic. A bare `INSERT` answered "duplicate key value violates unique
    constraint" — a database error that came up to the workflow, which died. Seen on the
    bench of 2026-09-24. AGENTS.md requires it: replayable without a double effect."""
    from choregos_api.db.models import GatewayKeyRow
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    first = await _run_prepare(setup)
    second = await _run_prepare(setup)
    assert first["run_id"] == second["run_id"], "the run id is deterministic"

    async with session_scope() as session:
        rows = (await session.execute(select(GatewayKeyRow))).scalars().all()
    assert len(rows) == 1, "a single key, not two — nor a constraint error"


class _ScriptedExecutor(_QueueingExecutor):
    """A sequence of states per poll, then the last one forever; `cancel` is noted."""

    def __init__(self, *states: ExecStatus) -> None:
        super().__init__()
        self.states = list(states)
        self.cancelled: list[str] = []

    async def status(self, ref: ExecRef) -> ExecStatus:
        if len(self.states) > 1:
            return self.states.pop(0)
        return self.states[0]

    async def cancel(self, ref: ExecRef) -> None:
        self.cancelled.append(ref.name)


QUEUED = ExecStatus(state="pending", message="waiting for a slot (ceiling 2 per namespace)")
RUNNING = ExecStatus(state="running")
FINISHED = ExecStatus(state="succeeded")


async def _started(setup: Fixture, executor: Any) -> str:
    from choregos_orchestrator.activities import stage as activities

    setup.adapters.executor = executor
    plan = await _run_prepare(setup)
    run_id = str(plan["run_id"])
    await activities.start_run(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "run_id": run_id,
            "stage_input": plan["stage_input"],
        }
    )
    return run_id


async def test_the_wait_in_the_queue_does_not_count_against_the_stage_budget(setup: Fixture) -> None:
    """Two HR runs "exceeded 20 min" without a pod having run (bench of 2026-09-25):
    their Job was waiting for a slot. Here the queue lasts ten times the stage budget, and the
    run still ends well."""
    from choregos_orchestrator.activities import stage as activities

    executor = _ScriptedExecutor(*([QUEUED] * 20), RUNNING, FINISHED)
    run_id = await _started(setup, executor)
    result = await activities.await_run(
        {
            "project_id": setup.project_id,
            "run_id": run_id,
            "timeout_minutes": 0.002,  # 0.12 s of stage…
            "queue_timeout_minutes": 1,
            "poll_seconds": 0.01,  # …after 0.2 s of queue
        }
    )
    assert result["status"] != "timed_out", result
    assert executor.cancelled == [], "nothing to clean up: the run ran"


async def test_a_never_admitted_run_fails_on_the_queue_and_removes_its_job(setup: Fixture) -> None:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities import stage as activities

    executor = _ScriptedExecutor(QUEUED)
    run_id = await _started(setup, executor)
    result = await activities.await_run(
        {
            "project_id": setup.project_id,
            "run_id": run_id,
            "timeout_minutes": 10,
            "queue_timeout_minutes": 0.002,
            "poll_seconds": 0.01,
        }
    )
    assert result["status"] == "queue_timed_out"
    assert result["result"]["reason"] == "queue_timeout"
    assert "never admitted" in result["result"]["summary"]
    assert executor.cancelled == [f"run-{run_id}"], (
        "the suspended Job is removed: it will not block the next ones"
    )
    async with session_scope() as session:
        run = await session.get(Run, run_id)
        assert run is not None and run.status == "failed" and run.ended_at is not None


async def test_a_run_that_exceeds_its_budget_also_removes_its_job(setup: Fixture) -> None:
    from choregos_orchestrator.activities import stage as activities

    executor = _ScriptedExecutor(RUNNING)
    run_id = await _started(setup, executor)
    result = await activities.await_run(
        {"project_id": setup.project_id, "run_id": run_id, "timeout_minutes": 0.002, "poll_seconds": 0.01}
    )
    assert result["status"] == "timed_out" and result["result"]["reason"] == "timeout"
    assert executor.cancelled == [f"run-{run_id}"]
