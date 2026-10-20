"""#750 (S38-13b): an executor that refuses a run on the configuration fails the run, once.

Before: `start_run` let `ConfigurationError` out as a plain error, Temporal retried it five times,
and the run row stayed `queued`, with no reason and no timeline event.
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_adapters.errors import ConfigurationError
from choregos_adapters.fakes.executor import FakeExecutor
from temporalio.exceptions import ApplicationError

from .conftest import Fixture

pytestmark = pytest.mark.asyncio

REASON = (
    "the run asks for RuntimeClass `gvisor`, the warm pool `choregos-runner` runs `the cluster default`: "
    "set `runner.runtimeClass` to match the policy"
)


async def _prepare(setup: Fixture) -> dict[str, Any]:
    from choregos_orchestrator.activities import stage as activites

    return await activites.prepare_stage(
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


async def _start(setup: Fixture, plan: dict[str, Any]) -> dict[str, Any]:
    from choregos_orchestrator.activities import stage as activites

    return await activites.start_run(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "run_id": str(plan["run_id"]),
            "stage_input": plan["stage_input"],
        }
    )


async def test_a_configuration_refusal_fails_the_run_and_is_not_retried(setup: Fixture) -> None:
    from choregos_api.db.models import Event, Run
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.execution import START_REFUSED
    from sqlalchemy import select

    executor = FakeExecutor()
    executor.start_error = ConfigurationError(REASON)
    setup.adapters.executor = executor
    plan = await _prepare(setup)
    run_id = str(plan["run_id"])

    with pytest.raises(ApplicationError) as raised:
        await _start(setup, plan)
    # Temporal does not retry an error flagged so: one attempt, and the type the workflow can read.
    assert raised.value.non_retryable is True
    assert raised.value.type == START_REFUSED
    assert "RuntimeClass `gvisor`" in str(raised.value)
    assert executor.start_calls_by_run[run_id] == 1

    async with session_scope() as session:
        run = await session.get(Run, run_id)
        assert run is not None
        assert run.status == "failed", "not `queued` forever"
        assert run.ended_at is not None
        assert run.result is not None
        assert run.result["status"] == "failed"
        assert run.result["reason"] == START_REFUSED
        assert "RuntimeClass `gvisor`" in run.result["summary"]
        events = (
            (await session.execute(select(Event).where(Event.work_item_id == setup.work_item_id)))
            .scalars()
            .all()
        )
    finished = [e for e in events if e.type.endswith("run.finished")]
    assert len(finished) == 1
    assert finished[0].payload["status"] == "failed"
    assert "RuntimeClass `gvisor`" in finished[0].payload["reason"]


async def test_another_error_is_still_retried(setup: Fixture) -> None:
    """Only a configuration refusal is final: an upstream hiccup keeps its retries and its run."""
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    executor = FakeExecutor()
    executor.start_error = RuntimeError("the cluster API answered 503")
    setup.adapters.executor = executor
    plan = await _prepare(setup)
    with pytest.raises(RuntimeError):
        await _start(setup, plan)
    async with session_scope() as session:
        run = await session.get(Run, str(plan["run_id"]))
        assert run is not None and run.status == "queued"
