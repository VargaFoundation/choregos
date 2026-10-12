"""At the end of every run, success or not, the pod's last lines and how it ended are kept (S36-02).

A failure before the ACP journal starts — an image that does not pull, a CLI that crashes, an
OOM kill — left nothing behind: the journal was empty and the pod went away with its Job.
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_contracts import StageResult, StageStatus
from choregos_core.domain import ExecRef

from .conftest import Fixture

pytestmark = pytest.mark.asyncio

# Assembled at run time: a whole fake token would trip the repository's secret scanner.
TOKEN = "ghs_" + "x1" * 15


async def _prepare_and_start(setup: Fixture) -> str:
    from choregos_orchestrator.activities import stage as activities

    plan = await activities.prepare_stage(
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
    await activities.start_run(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "stage_input": plan["stage_input"],
        }
    )
    return str(plan["run_id"])


async def _await(setup: Fixture, run_id: str) -> dict[str, Any]:
    from choregos_orchestrator.activities import stage as activities

    return await activities.await_run(
        {"project_id": setup.project_id, "run_id": run_id, "timeout_minutes": 1, "poll_seconds": 0.01}
    )


async def _archive(run_id: str) -> dict[str, Any] | None:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        run = await session.get(Run, run_id)
        assert run is not None
        return run.log_archive


async def test_a_crashing_pod_leaves_its_reason_and_last_lines_with_the_token_masked(setup: Fixture) -> None:
    setup.adapters.executor.crash_next(
        exit_code=137,
        reason="OOMKilled",
        log_lines=["runner: starting", f"env: GITHUB_TOKEN={TOKEN}", "agent: loading embeddings", "Killed"],
    )
    run_id = await _prepare_and_start(setup)
    outcome = await _await(setup, run_id)
    assert outcome["status"] == "failed"

    archive = await _archive(run_id)
    assert archive is not None, "the run is gone; what killed it must not be"
    assert (archive["state"], archive["exit_code"], archive["reason"]) == ("failed", 137, "OOMKilled")
    assert archive["lines"][-2:] == ["agent: loading embeddings", "Killed"]
    assert "env: GITHUB_TOKEN=[REDACTED]" in archive["lines"]
    assert TOKEN not in str(archive)


async def test_a_successful_run_is_archived_too(setup: Fixture) -> None:
    setup.adapters.executor.queue_result(StageResult(status=StageStatus.DONE, summary="done"))
    run_id = await _prepare_and_start(setup)
    assert (await _await(setup, run_id))["status"] == "finished"
    archive = await _archive(run_id)
    assert archive is not None
    assert (archive["state"], archive["reason"]) == ("succeeded", "Completed")
    assert archive["lines"] == setup.adapters.executor.log_lines


async def test_a_replayed_await_keeps_the_first_archive(setup: Fixture) -> None:
    """Idempotence (ADR 0008): Temporal may replay `await_run`; the archive is written once."""
    setup.adapters.executor.crash_next(reason="Error", exit_code=1, log_lines=["first"])
    run_id = await _prepare_and_start(setup)
    await _await(setup, run_id)
    first = await _archive(run_id)
    setup.adapters.executor.run_logs[run_id] = ["something else"]
    await _await(setup, run_id)
    assert await _archive(run_id) == first


async def test_a_cancelled_run_is_archived_before_its_pod_is_deleted(setup: Fixture) -> None:
    from choregos_adapters.fakes.executor import FakeExecutor
    from choregos_orchestrator.activities import stage as activities

    class _DeletesLogsOnCancel(FakeExecutor):
        async def cancel(self, ref: ExecRef) -> None:
            self.run_logs[ref.run_id or ""] = []
            await super().cancel(ref)

    executor = _DeletesLogsOnCancel()
    executor.crash_next(reason="Error", exit_code=1, log_lines=["still running", "doing work"])
    setup.adapters.executor = executor
    run_id = await _prepare_and_start(setup)
    await activities.cancel_run({"project_id": setup.project_id, "run_id": run_id})
    archive = await _archive(run_id)
    assert archive is not None and archive["lines"] == ["still running", "doing work"]


async def test_an_executor_without_readable_logs_does_not_stop_the_run(setup: Fixture) -> None:
    from choregos_adapters.fakes.executor import FakeExecutor

    class _NoLogs(FakeExecutor):
        def logs(self, ref: ExecRef) -> Any:
            raise NotImplementedError

    executor = _NoLogs()
    executor.crash_next(reason="Error", exit_code=1)
    setup.adapters.executor = executor
    run_id = await _prepare_and_start(setup)
    assert (await _await(setup, run_id))["status"] == "failed"
    archive = await _archive(run_id)
    assert archive is not None and archive["reason"] == "Error"
    assert archive["error"] and "NotImplementedError" in archive["error"]
