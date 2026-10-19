"""A run the executor no longer knows ends the wait, instead of the stage budget (S35-02c).

On the dev tenant, on 2026-10-18, a review's SandboxClaim was gone while the executor was being
redeployed. `agent_sandbox` answered `unknown` ("SandboxClaim not found") on every poll, `unknown` is
not `finished`, and `await_run` waited 1 h 32 for a 35 min budget, Temporal retries included, with
nothing running and no token spent.
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_contracts import ExecutorKind, StageResult, StageStatus
from choregos_core.domain import ExecRef, ExecStatus, StageJobSpec

from .conftest import Fixture
from .test_result_from_journal import _journal, _started_run

pytestmark = pytest.mark.asyncio


class _ScriptedExecutor:
    """Answers the states it is given, one per poll, then repeats the last one."""

    kind = ExecutorKind.AGENT_SANDBOX
    capabilities: frozenset[str] = frozenset()

    def __init__(self, *states: str) -> None:
        self.states = list(states)
        self.polls = 0
        self.cancelled = 0

    async def start(self, spec: StageJobSpec) -> ExecRef:
        return ExecRef(
            kind=self.kind, name=f"claim-{spec.run_id}", namespace=spec.namespace, run_id=spec.run_id
        )

    async def status(self, ref: ExecRef) -> ExecStatus:
        state = self.states[min(self.polls, len(self.states) - 1)]
        self.polls += 1
        message = "SandboxClaim not found" if state == "unknown" else ""
        return ExecStatus(state=state, message=message)  # type: ignore[arg-type]

    async def cancel(self, ref: ExecRef) -> None:
        self.cancelled += 1

    async def logs(self, ref: ExecRef) -> Any:
        if False:
            yield ""


async def _await(setup: Fixture, run_id: str) -> dict[str, Any]:
    from choregos_orchestrator.activities import stage as activities

    # 0.05 min of budget is 300 polls: the old wait went through all of them, then said "timed_out".
    return await activities.await_run(
        {"project_id": setup.project_id, "run_id": run_id, "timeout_minutes": 0.05, "poll_seconds": 0.01}
    )


async def test_a_run_the_executor_lost_fails_within_a_few_polls(setup: Fixture) -> None:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.execution import LOST_POLLS

    executor = _ScriptedExecutor("running", "unknown")
    setup.adapters.executor = executor
    run_id = await _started_run(setup)

    outcome = await _await(setup, run_id)
    assert outcome["status"] == "lost", "a run nothing runs any more must not wait out its budget"
    result = StageResult.model_validate(outcome["result"])
    assert result.status == StageStatus.FAILED and result.reason == "lost"
    assert "SandboxClaim not found" in result.summary, "the executor's own words reach the ticket"
    assert executor.polls <= 1 + LOST_POLLS + 1, "the archive may ask once more, nothing beyond"
    assert executor.cancelled == 1, "whatever is left at the executor is cleaned up"
    async with session_scope() as session:
        run = await session.get(Run, run_id)
        assert run is not None and run.status == "failed" and run.ended_at is not None


async def test_a_lost_run_that_journaled_its_result_keeps_it(setup: Fixture) -> None:
    setup.adapters.executor = _ScriptedExecutor("unknown")
    run_id = await _started_run(setup)
    done = StageResult(status=StageStatus.DONE, summary="review approved")
    await _journal(run_id, {"stage_result": done.model_dump(mode="json", by_alias=True)})

    outcome = await _await(setup, run_id)
    assert outcome["status"] == "finished", (
        "a claim collected after the run posted its journal is not a failure"
    )
    assert StageResult.model_validate(outcome["result"]).summary == "review approved"


async def test_a_brief_unknown_does_not_end_a_run_that_comes_back(setup: Fixture) -> None:
    from choregos_orchestrator.activities.execution import LOST_POLLS

    # One poll short of the limit, then the run is seen again and ends on its own.
    executor = _ScriptedExecutor("running", *["unknown"] * (LOST_POLLS - 1), "running", "unknown", "failed")
    setup.adapters.executor = executor
    run_id = await _started_run(setup)

    outcome = await _await(setup, run_id)
    assert outcome["status"] == "failed", "unknowns that are not in a row are not a lost run"
