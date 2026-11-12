"""A run a person stopped ends its stage within one poll and goes to a person (S64-03, ADR 0142 §2-3).

The API writes the run's result as stopped (`services/run_cancel.py`) and starts `RunCancellation`;
`await_run` reads the result at its next poll; the interpreter books the spend once, does not retry
the stage, does not count the attempt, and the work item waits with "run stopped by <who>: <reason>".
The histories of both workflows replay (`tests/replay/histories`).
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import time
from typing import Any

import pytest
from choregos_contracts import StageResult, StageStatus
from choregos_core.domain import ExecStatus

from .conftest import Fixture, recording_history
from .test_interpreter import _runs, _wait_state, start

pytestmark = pytest.mark.asyncio

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"
WHO = "ops@varga.dev"


def keep_the_first_run_running(setup: Fixture) -> None:
    """The fake executor's first run never ends on its own: it is in flight until someone stops it.
    The runs after it end as the fake scripts them."""
    executor = setup.adapters.executor
    real_start = executor.start

    async def start_and_keep_running(spec: Any) -> Any:
        first = not executor.started
        ref = await real_start(spec)
        if first:
            executor.results.pop(spec.run_id, None)
            executor.statuses[spec.run_id] = ExecStatus(state="running")
        return ref

    executor.start = start_and_keep_running


async def _in_flight(setup: Fixture, *, timeout: float = 30) -> str:
    """The id of the work item's run once the executor runs it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        started = setup.adapters.executor.started
        if started:
            return str(started[0].run_id)
        await asyncio.sleep(0.05)
    raise AssertionError("no run started")


async def stop_as_the_api_does(run_id: str, reason: str) -> dict[str, Any]:
    """`POST /runs/{id}/cancel`, as its service plays it: the result written, `run.cancel` audited."""
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_api.rbac import Principal, Role
    from choregos_api.services.run_cancel import cancel_run_now

    principal = Principal(user_id="u-ops", email=WHO, org_roles={"varga": Role.ORG_ADMIN})
    async with session_scope() as session:
        run = await session.get(Run, run_id)
        assert run is not None and run.status in {"queued", "running"}, run and run.status
        stopped = await cancel_run_now(session, principal, run, reason)
        return dict(stopped.result or {})


async def _ledger_lines(run_id: str) -> int:
    from choregos_api.db.models import CostLedger
    from choregos_api.db.session import session_scope
    from sqlalchemy import func, select

    async with session_scope() as session:
        return int(
            (
                await session.execute(
                    select(func.count()).select_from(CostLedger).where(CostLedger.run_id == run_id)
                )
            ).scalar_one()
        )


async def _requests(setup: Fixture) -> list[dict[str, Any]]:
    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (
            await session.execute(select(HumanRequest).where(HumanRequest.work_item_id == setup.work_item_id))
        ).scalars()
        return [dict(row.payload or {}) for row in rows]


async def _run_status(run_id: str) -> str:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        run = await session.get(Run, run_id)
        assert run is not None
        return run.status


@pytest.fixture
def one_second_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    """`await_run` polls every `heartbeat_seconds`: one second here, so "within one poll" is short."""
    from choregos_orchestrator.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_HEARTBEAT_SECONDS", "1")
    reset_settings_cache()


async def test_a_stopped_run_ends_its_stage_and_the_work_item_waits_for_a_person(
    setup: Fixture, temporal_env: Any, worker_factory: Any, one_second_polls: None
) -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    keep_the_first_run_running(setup)
    async with worker_factory():
        handle = await start(temporal_env, setup)
        run_id = await _in_flight(setup)
        await asyncio.sleep(1.5)  # a poll or two of a run that runs
        assert await _run_status(run_id) in {"queued", "running"}

        written = await stop_as_the_api_does(run_id, "it is editing the wrong repo")
        assert written["summary"] == f"run stopped by {WHO}: it is editing the wrong repo"
        cancellation = await temporal_env.client.start_workflow(
            "RunCancellation",
            {"project_id": setup.project_id, "run_id": run_id},
            id=f"run-cancel-{run_id}",
            task_queue="test",
        )
        assert await cancellation.result() == {"run_id": run_id, "cancelled": True}
        await _wait_state(handle, "needs_human", timeout=30)

        assert setup.adapters.executor.cancelled == [run_id], "the executor is told once"
        assert await _runs(setup) == 1, "a stopped run is not retried"
        assert await _run_status(run_id) == "cancelled"
        assert await _ledger_lines(run_id) == 1, "its spend is booked once"
        asked = [r for r in await _requests(setup) if r.get("run_id") == run_id]
        assert len(asked) == 1 and asked[0]["reason"] == "stopped_by_person"
        assert asked[0]["question"].startswith(f"run stopped by {WHO}: it is editing the wrong repo")
        status = await handle.query("status")
        assert status["current_run"] is None and not status["stopped"]

        await handle.signal("control", {"action": "stop"})
        await handle.result()
        history = await handle.fetch_history()
        cancelled_history = await cancellation.fetch_history()

    replayer = Replayer(workflows=ALL_WORKFLOWS)
    await replayer.replay_workflow(history)
    await replayer.replay_workflow(cancelled_history)
    if recording_history():
        for name, recorded in (
            ("wi-run-stopped-by-a-person-S64-03.json", history),
            ("run-cancellation-S64-03.json", cancelled_history),
        ):
            target = HISTORIES / name
            target.write_text(json.dumps(json.loads(recorded.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_a_stopped_run_does_not_count_as_an_attempt(
    setup: Fixture, temporal_env: Any, worker_factory: Any, one_second_polls: None
) -> None:
    """`t-refine` allows two attempts. Stopped on its first, then replayed by a person: the replay
    plays on with both attempts it would have had, so two failures before it escalates again — three
    runs. Counted, the stopped attempt would leave one: two runs."""
    keep_the_first_run_running(setup)
    executor = setup.adapters.executor
    async with worker_factory():
        handle = await start(temporal_env, setup)
        run_id = await _in_flight(setup)
        await stop_as_the_api_does(run_id, "not now")
        await _wait_state(handle, "needs_human", timeout=30)
        assert await _runs(setup) == 1

        executor.queue_result(StageResult(status=StageStatus.FAILED, summary="tests red", reason="tests"))
        executor.queue_result(StageResult(status=StageStatus.FAILED, summary="lint red", reason="lint"))
        await handle.signal("control", {"action": "rerun_stage"})
        await _wait_until_runs(setup, 3)
        await _wait_state(handle, "needs_human", timeout=30)
        await handle.signal("control", {"action": "stop"})
        await handle.result()

    assert await _runs(setup) == 3
    assert executor.cancelled == [], "nothing started `RunCancellation` here: the interpreter does not"


async def test_a_work_item_stopped_with_its_run_ends(
    setup: Fixture, temporal_env: Any, worker_factory: Any, one_second_polls: None
) -> None:
    """The work item's `stop` (ADR 0142 §3): the flag and the run's result. The stage is not taken to
    a person: the work item ends as a stop does, without a request."""
    keep_the_first_run_running(setup)
    async with worker_factory():
        handle = await start(temporal_env, setup)
        run_id = await _in_flight(setup)
        await handle.signal("control", {"action": "stop", "reason": "wrong ticket"})
        await stop_as_the_api_does(run_id, "wrong ticket")
        outcome = await asyncio.wait_for(handle.result(), timeout=30)

    assert outcome["stopped"] is True and outcome["state"] == "inbox"
    assert await _runs(setup) == 1
    assert await _ledger_lines(run_id) == 1
    assert [r for r in await _requests(setup) if r.get("run_id") == run_id] == []


async def test_before_the_marker_a_stopped_run_was_retried(
    setup: Fixture,
    temporal_env: Any,
    worker_factory: Any,
    one_second_polls: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A history recorded before `run-stopped-goes-to-a-person` keeps its old decision on replay:
    the stopped run was a failed attempt, and the stage played again."""
    from choregos_orchestrator.workflows import interpreter

    real = interpreter.workflow.patched
    monkeypatch.setattr(
        interpreter.workflow,
        "patched",
        lambda marker: False if marker == interpreter.RUN_STOPPED_GOES_TO_A_PERSON else real(marker),
    )
    keep_the_first_run_running(setup)
    async with worker_factory():
        handle = await start(temporal_env, setup)
        run_id = await _in_flight(setup)
        await stop_as_the_api_does(run_id, "not now")
        await _wait_until_runs(setup, 2)
        await handle.signal("control", {"action": "stop"})
        await handle.result()


async def _wait_until_runs(setup: Fixture, count: int, *, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await _runs(setup) >= count:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"never {count} runs (now {await _runs(setup)})")


async def test_await_run_returns_the_stop_within_one_poll(setup: Fixture) -> None:
    """The run keeps running on its executor; the written result ends the wait at the next poll."""
    from choregos_orchestrator.activities import stage as activities

    keep_the_first_run_running(setup)
    plan = await activities.prepare_stage(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "transition_id": "t-refine",
            "role": "refine",
            "actor": "refiner",
            "from_state": "inbox",
            "to_state": "awaiting_spec_approval",
            "attempt": 1,
        }
    )
    run_id = str(plan["run_id"])
    await activities.start_run(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "stage_input": plan["stage_input"],
        }
    )
    poll = 0.3
    waiting = asyncio.create_task(
        activities.await_run(
            {"project_id": setup.project_id, "run_id": run_id, "timeout_minutes": 5, "poll_seconds": poll}
        )
    )
    await asyncio.sleep(3 * poll)
    assert not waiting.done(), "the run is in flight: nothing ends the wait yet"

    await stop_as_the_api_does(run_id, "stop it")
    stopped_at = time.monotonic()
    awaited = await asyncio.wait_for(waiting, timeout=10)

    # The run never ends on its own here: what is measured is that the stop ends the wait at a poll, not
    # at the run's end. A loaded CI runner took 2.2 s for one poll of 0.3 s, hence the margin.
    assert time.monotonic() - stopped_at < poll + 3.0, "within a poll, not at the run's end"
    assert awaited["status"] == "finished"
    assert awaited["result"]["reason"] == "stopped_by_person"


async def test_two_closings_of_one_run_book_its_spend_once(setup: Fixture) -> None:
    """The interpreter's `collect_spend` and a stop's `cancel_run` may close the same run at once: the
    second, holding a copy read before the first committed, books nothing."""
    from choregos_api.db.models import Run
    from choregos_orchestrator.activities import stage as activities
    from choregos_orchestrator.activities.base import db, project_bundle
    from choregos_orchestrator.activities.outcome import book_spend
    from choregos_orchestrator.config import get_settings

    plan = await activities.prepare_stage(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "transition_id": "t-refine",
            "role": "refine",
            "actor": "refiner",
            "from_state": "inbox",
            "to_state": "awaiting_spec_approval",
            "attempt": 1,
        }
    )
    run_id = str(plan["run_id"])
    async with db() as late:
        bundle = await project_bundle(late, setup.project_id)
        stale = await late.get(Run, run_id)
        assert stale is not None and not stale.spend_collected
        await activities.collect_spend({"project_id": setup.project_id, "run_id": run_id})  # first, committed
        await book_spend(late, bundle, stale, get_settings())

    assert await _ledger_lines(run_id) == 1
