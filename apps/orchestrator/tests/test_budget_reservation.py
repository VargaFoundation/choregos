"""A run's key is minted at what is left; below the floor the stage does not start (S27-02, ADR 0047).

Before, every key was minted at the full stage budget, whatever the ticket had left, and the ticket
budget was only checked after the stage — an overshoot of up to one full stage per stage.
"""

from __future__ import annotations

import json
import os
import pathlib
from datetime import timedelta
from typing import Any

import pytest

from .conftest import Fixture
from .test_interpreter import _runs, _wait_state, _wait_until, start

pytestmark = pytest.mark.asyncio

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"


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


async def _engine(setup: Fixture) -> Any:
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.base import project_bundle

    async with session_scope() as session:
        return (await project_bundle(session, setup.project_id)).engine


async def spend(setup: Fixture, usd: float, *, work_item: bool = True, kind: str = "model") -> None:
    """A line in the cost ledger, as `collect_spend` (model) or the tool route (tool) writes it."""
    from choregos_api.db.models import CostLedger
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    async with session_scope() as session:
        session.add(
            CostLedger(
                project_id=setup.project_id,
                work_item_id=setup.work_item_id if work_item else None,
                kind=kind,
                cost_usd=usd,
                ts=utcnow(),
            )
        )


async def leave_on_ticket(setup: Fixture, left: float) -> None:
    """Spends the ticket's budget down to `left` — half in models, half in tools."""
    budget = (await _engine(setup)).budget_ticket("M")
    await spend(setup, (budget - left) / 2)
    await spend(setup, (budget - left) / 2, kind="tool")


async def _run_count(setup: Fixture) -> int:
    return await _runs(setup)


async def test_a_ticket_with_ten_cents_left_gets_a_ten_cent_key(setup: Fixture) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage, start_run

    await leave_on_ticket(setup, 0.10)
    prepared = await prepare_stage(_plan(setup))
    assert "refused" not in prepared
    assert prepared["stage_input"]["budget"]["usd"] == pytest.approx(0.10)
    # The key is minted when the run starts, for its Secret (#523).
    await start_run(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "stage_input": prepared["stage_input"],
        }
    )
    [key] = setup.adapters.gateway.keys.values()
    assert key.budget_usd == pytest.approx(0.10), "the gateway's hard cap is the reservation"


async def test_at_zero_no_run_starts(setup: Fixture) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    await leave_on_ticket(setup, 0.0)
    prepared = await prepare_stage(_plan(setup))
    assert prepared["refused"]["reason"] == "budget"
    assert "bound by ticket" in prepared["refused"]["detail"]
    assert not setup.adapters.gateway.keys, "no key is minted"
    assert await _run_count(setup) == 0, "no run is created"


async def test_an_open_key_is_reserved(setup: Fixture) -> None:
    """A key still open may spend up to its cap: what is left excludes it."""
    from choregos_api.db.models import GatewayKeyRow, Run
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow
    from choregos_orchestrator.activities.stage import prepare_stage

    await leave_on_ticket(setup, 2.0)
    async with session_scope() as session:
        session.add(
            Run(
                id="run-open", work_item_id=setup.work_item_id, project_id=setup.project_id, stage_role="plan"
            )
        )
        await session.flush()
        session.add(
            GatewayKeyRow(
                key_id="k-open",
                run_id="run-open",
                project_id=setup.project_id,
                budget_usd=1.5,
                expires_at=utcnow() + timedelta(hours=1),
            )
        )
    prepared = await prepare_stage(_plan(setup))
    assert prepared["stage_input"]["budget"]["usd"] == pytest.approx(0.5)


async def test_the_project_day_bounds_the_key(setup: Fixture) -> None:
    from choregos_api.db.models import PolicyDef
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.stage import prepare_stage
    from sqlalchemy import select

    async with session_scope() as session:
        row = (
            await session.execute(
                select(PolicyDef).where(PolicyDef.project_id == setup.project_id, PolicyDef.is_active)
            )
        ).scalar_one()
        doc = json.loads(json.dumps(row.json_doc))
        doc.setdefault("budgets", {})["daily_project_usd"] = 1.0
        row.json_doc = doc
    await spend(setup, 0.7, work_item=False)  # another ticket of the project, today
    prepared = await prepare_stage(_plan(setup))
    assert prepared["stage_input"]["budget"]["usd"] == pytest.approx(0.3)


async def test_a_ticket_with_nothing_left_goes_to_a_person_without_a_run(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    await leave_on_ticket(setup, 0.0)
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "needs_human")
        await _wait_until(handle, "a person is asked", lambda s: s["pending_request"])
        assert await _run_count(setup) == 0
        assert not setup.adapters.executor.started
        await handle.signal("control", {"action": "stop"})
        await handle.result()
        history = await handle.fetch_history()

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if os.environ.get("CHOREGOS_ARCHIVER_HISTORIQUE"):
        target = HISTORIES / "wi-budget-reservation-S27-02.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")
