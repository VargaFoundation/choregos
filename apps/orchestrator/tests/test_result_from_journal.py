"""A result the runner could not post is read from its journal (#410).

The Tekton `result-url` pointed at a `result.json` nothing uploaded: `TektonExecutor.fetch_result`
never found it, and a failed post lost the stage result. The runner now journals the whole result
before posting it, and the journal is what the API already holds.
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_contracts import ExecutorKind, StageResult, StageStatus
from choregos_core.domain import ExecRef, ExecStatus, StageJobSpec

from .conftest import Fixture

pytestmark = pytest.mark.asyncio


class _TektonWithoutUpload:
    """A Tekton run that finished, whose `result-url` leads nowhere — as in production."""

    kind = ExecutorKind.TEKTON
    capabilities: frozenset[str] = frozenset()

    def __init__(self) -> None:
        self.fetches = 0

    async def start(self, spec: StageJobSpec) -> ExecRef:
        return ExecRef(kind=self.kind, name=f"pr-{spec.run_id}", namespace=spec.namespace, run_id=spec.run_id)

    async def status(self, ref: ExecRef) -> ExecStatus:
        return ExecStatus(state="succeeded", exit_code=0, result_url="")

    async def fetch_result(self, ref: ExecRef) -> StageResult | None:
        self.fetches += 1
        return None

    async def cancel(self, ref: ExecRef) -> None:
        return None

    async def logs(self, ref: ExecRef) -> Any:
        if False:
            yield ""


async def _started_run(setup: Fixture) -> str:
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


async def _journal(run_id: str, *payloads: dict[str, Any]) -> None:
    from choregos_api.db.models import RunEvent
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    async with session_scope() as session:
        for seq, payload in enumerate(payloads, start=1):
            session.add(RunEvent(run_id=run_id, seq=seq, type="run.result", payload=payload, ts=utcnow()))


async def _await(setup: Fixture, run_id: str) -> dict[str, Any]:
    from choregos_orchestrator.activities import stage as activities

    return await activities.await_run(
        {"project_id": setup.project_id, "run_id": run_id, "timeout_minutes": 1, "poll_seconds": 0.01}
    )


async def test_a_tekton_run_whose_post_failed_still_yields_its_result(setup: Fixture) -> None:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    setup.adapters.executor = _TektonWithoutUpload()
    run_id = await _started_run(setup)
    result = StageResult(status=StageStatus.DONE, summary="credit notes deducted")
    await _journal(
        run_id,
        {
            "status": "done",
            "summary": result.summary,
            "stage_result": result.model_dump(mode="json", by_alias=True),
        },
    )

    outcome = await _await(setup, run_id)
    assert outcome["status"] == "finished", "the result was not lost with the failed post"
    assert StageResult.model_validate(outcome["result"]).summary == "credit notes deducted"
    async with session_scope() as session:
        run = await session.get(Run, run_id)
        assert run is not None and run.result is not None and run.result["summary"] == "credit notes deducted"


async def test_an_old_runner_journal_without_the_result_still_falls_back_to_the_executor(
    setup: Fixture,
) -> None:
    executor = _TektonWithoutUpload()
    setup.adapters.executor = executor
    run_id = await _started_run(setup)
    await _journal(run_id, {"status": "done", "summary": "older runner: no stage_result"})
    outcome = await _await(setup, run_id)
    assert outcome["status"] == "missing_result"
    assert executor.fetches == 1, "the executor is still asked when the journal has nothing"


async def test_a_journaled_result_that_does_not_validate_is_not_trusted(setup: Fixture) -> None:
    setup.adapters.executor = _TektonWithoutUpload()
    run_id = await _started_run(setup)
    await _journal(run_id, {"stage_result": {"status": "not-a-status"}})
    assert (await _await(setup, run_id))["status"] == "missing_result"
