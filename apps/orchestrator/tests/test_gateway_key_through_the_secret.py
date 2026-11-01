"""The gateway key reaches the runner through the run's Secret, and dies however the run ends (#523).

S22-37 (#519) took the key out of every Temporal payload, but the runner still read it from
`GET /internal/runs/{id}/input`, so it sat on the `runs` row for the run's lifetime (and in any
backup taken meanwhile). And a run that ended without `collect_spend` — its interpreter cancelled,
or dead because `await_run` gave up — kept a live key until its TTL.

Now `prepare_stage` only reserves the key's cap; `start_run` mints the key for the executor, which
puts it in the run's Secret; the row never holds it. A dying interpreter — cancelled, or failed
because `await_run` gave up — books its run's spend and revokes its key (`record_workflow_failure`),
and so does `cancel_run`.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from .conftest import Fixture, refine_result
from .test_interpreter import _wait_until, scripted, start

pytestmark = pytest.mark.asyncio


def _plan(setup: Fixture, attempt: int = 1) -> dict[str, Any]:
    return {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "transition_id": "t-refine",
        "role": "refine",
        "from_state": "inbox",
        "to_state": "awaiting_spec_approval",
        "actor": "refiner",
        "attempt": attempt,
    }


async def _prepare_and_start(setup: Fixture, attempt: int = 1) -> str:
    from choregos_orchestrator.activities import stage as activities

    prepared = await activities.prepare_stage(_plan(setup, attempt))
    await activities.start_run(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "stage_input": prepared["stage_input"],
        }
    )
    return str(prepared["run_id"])


async def _run(run_id: str) -> Any:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        run = await session.get(Run, run_id)
        assert run is not None
        session.expunge(run)
        return run


async def _key_rows(setup: Fixture) -> dict[str, Any]:
    from choregos_api.db.models import GatewayKeyRow
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (
            (await session.execute(select(GatewayKeyRow).where(GatewayKeyRow.project_id == setup.project_id)))
            .scalars()
            .all()
        )
        return {row.key_id: (row.revoked, row.budget_usd) for row in rows}


async def _open_reservations(setup: Fixture) -> float:
    from choregos_api.db.session import session_scope
    from choregos_api.services.budgets import open_reservations

    async with session_scope() as session:
        return await open_reservations(session, work_item_id=setup.work_item_id)


# ─────────────────────── the key is never on the row ───────────────────────


async def test_a_prepared_run_reserves_its_cap_before_its_key_exists(setup: Fixture) -> None:
    """No key yet, but the money is held: another run cannot take it in between."""
    from choregos_orchestrator.activities import stage as activities

    prepared = await activities.prepare_stage(_plan(setup))
    assert not setup.adapters.gateway.keys
    budget = prepared["stage_input"]["budget"]["usd"]
    assert await _open_reservations(setup) == pytest.approx(budget)

    await activities.start_run(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "stage_input": prepared["stage_input"],
        }
    )
    [key_id] = setup.adapters.gateway.keys
    assert await _key_rows(setup) == {key_id: (False, pytest.approx(budget))}, (
        "the reservation became the key's row: held once, never twice"
    )
    assert (await _run(prepared["run_id"])).gateway_key_id == key_id


async def test_a_run_prepared_before_523_hands_its_row_key_to_the_secret(setup: Fixture) -> None:
    """A run prepared by an older orchestrator has its key on the row: it is used, and leaves it."""
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities import stage as activities

    prepared = await activities.prepare_stage(_plan(setup))
    async with session_scope() as session:
        run = await session.get(Run, prepared["run_id"])
        assert run is not None
        run.stage_input = {**dict(run.stage_input or {}), "gateway_key": "sk-from-an-older-prepare"}
    await activities.start_run(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "stage_input": prepared["stage_input"],
        }
    )
    assert setup.adapters.executor.started[-1].gateway_key == "sk-from-an-older-prepare"
    assert not setup.adapters.gateway.keys, "no second key"
    assert (await _run(prepared["run_id"])).stage_input["gateway_key"] is None


# ─────────────────────── the key dies however the run ends ───────────────────────


async def test_cancel_run_books_the_spend_and_revokes_the_key(setup: Fixture) -> None:
    from choregos_orchestrator.activities import stage as activities

    run_id = await _prepare_and_start(setup)
    [key_id] = setup.adapters.gateway.keys
    await activities.cancel_run({"project_id": setup.project_id, "run_id": run_id})

    assert setup.adapters.gateway.revoked == [key_id]
    assert (await _key_rows(setup))[key_id][0] is True
    run = await _run(run_id)
    assert run.spend_collected is True, "booked once, here"
    assert run.status == "cancelled"
    assert await _open_reservations(setup) == 0.0

    await activities.cancel_run({"project_id": setup.project_id, "run_id": run_id})  # replayed
    assert setup.adapters.gateway.revoked == [key_id], "a revoked key is not revoked twice"


async def test_a_run_cancelled_before_it_started_frees_its_reservation(setup: Fixture) -> None:
    from choregos_orchestrator.activities import stage as activities

    prepared = await activities.prepare_stage(_plan(setup))
    assert await _open_reservations(setup) > 0
    await activities.cancel_run({"project_id": setup.project_id, "run_id": prepared["run_id"]})
    assert await _open_reservations(setup) == 0.0
    assert not setup.adapters.gateway.keys and not setup.adapters.gateway.revoked


async def test_a_dying_interpreter_revokes_the_key_of_its_run(setup: Fixture) -> None:
    """`await_run` gave up: the interpreter records its death, and its run's key dies with it."""
    from choregos_orchestrator.activities.interpretation import record_workflow_failure

    run_id = await _prepare_and_start(setup)
    [key_id] = setup.adapters.gateway.keys
    await record_workflow_failure(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "message": "await_run: schedule-to-close timeout",
            "activity": "await_run",
        }
    )
    assert setup.adapters.gateway.revoked == [key_id]
    assert (await _run(run_id)).spend_collected is True
    assert await _open_reservations(setup) == 0.0


async def test_a_gateway_that_cannot_answer_does_not_keep_the_key_alive_on_cancel(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The spend cannot be read: the key is still revoked, the run still cancelled."""
    from choregos_orchestrator.activities import stage as activities

    async def unreachable(key_id: str) -> Any:
        raise ConnectionError("gateway unreachable")

    run_id = await _prepare_and_start(setup)
    [key_id] = setup.adapters.gateway.keys
    monkeypatch.setattr(setup.adapters.gateway, "spend", unreachable)
    await activities.cancel_run({"project_id": setup.project_id, "run_id": run_id})
    assert setup.adapters.gateway.revoked == [key_id]
    assert (await _run(run_id)).status == "cancelled"


# ─────────────────────── a resumed run ───────────────────────


async def test_a_resumed_run_gets_its_fresh_key_through_the_executor_not_the_row(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    from .test_suspend_around_human import QUESTION, _answer, _can_suspend

    executor = _can_suspend(setup)
    scripted(setup.adapters, QUESTION, refine_result("spec"))
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_until(handle, "the question is asked", lambda s: s["pending_request"])
        await _answer(handle, setup)
        await _wait_until(handle, "the run resumed", lambda s: s["state"] == "awaiting_spec_approval")
        await handle.signal("control", {"action": "stop"})
        await handle.result()

    keys = setup.adapters.gateway.keys | dict.fromkeys(setup.adapters.gateway.revoked)
    assert len(keys) == 2, "one key per segment"
    [resumed_key] = executor.resumed_keys
    assert resumed_key is not None and resumed_key.startswith("sk-fake-")
    assert resumed_key != executor.started[0].gateway_key
    (run_id,) = [spec.run_id for spec in executor.started]
    assert resumed_key not in json.dumps((await _run(run_id)).stage_input)


# ─────────────────────── a cancelled interpreter ───────────────────────


async def test_a_cancelled_interpreter_revokes_the_key_of_its_run(setup: Fixture, temporal_env: Any) -> None:
    """Cancelled while it waits for its run, the interpreter dies through `record_workflow_failure`
    (the cancellation reaches it as the failure of `await_run`): the run's spend is booked and its
    key revoked there — an activity, no change under `workflows/`."""
    from temporalio import activity
    from temporalio.client import WorkflowFailureError

    from .kits.workers import worker_with

    waiting = asyncio.Event()

    @activity.defn(name="await_run")
    async def await_run_forever(payload: dict[str, Any]) -> dict[str, Any]:
        waiting.set()
        while True:  # the run never ends: only the cancellation stops the wait
            activity.heartbeat()
            await asyncio.sleep(0.05)

    async with worker_with(temporal_env, {"await_run": await_run_forever}):
        handle = await start(temporal_env, setup)
        await asyncio.wait_for(waiting.wait(), timeout=30)
        [key_id] = setup.adapters.gateway.keys
        await handle.cancel()
        with pytest.raises(WorkflowFailureError):
            await handle.result()

    assert setup.adapters.gateway.revoked == [key_id], "the key dies with the cancelled interpreter"
    (spec,) = setup.adapters.executor.started
    run = await _run(spec.run_id)
    assert run.spend_collected is True, "its spend is booked"
    assert await _open_reservations(setup) == 0.0


async def test_a_resumed_run_key_joins_the_project_day_team(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """The continuation's key is minted like any run key (S27-10): it joins the project's day team,
    so what the resumed run spends counts against the project's daily cap."""
    from choregos_orchestrator.activities.provisioning import project_team_id

    from .test_project_day_team import _daily
    from .test_suspend_around_human import QUESTION, _answer, _can_suspend

    await _daily(setup, 30.0)
    executor = _can_suspend(setup)
    scripted(setup.adapters, QUESTION, refine_result("spec"))
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_until(handle, "the question is asked", lambda s: s["pending_request"])
        await _answer(handle, setup)
        await _wait_until(handle, "the run resumed", lambda s: s["state"] == "awaiting_spec_approval")
        await handle.signal("control", {"action": "stop"})
        await handle.result()

    gateway = setup.adapters.gateway
    team = project_team_id(setup.project_id)
    [resumed_key] = executor.resumed_keys
    assert resumed_key is not None
    resumed_key_id = resumed_key.removeprefix("sk-fake-")
    first_key_id = str(executor.started[0].gateway_key).removeprefix("sk-fake-")
    assert gateway.key_team.get(first_key_id) == team
    assert gateway.key_team.get(resumed_key_id) == team, "the resumed run's key joins the day team"
