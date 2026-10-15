"""`attempts.max_stages_per_ticket` and the policy's attempt ceilings are enforced (S27-08, ADR 0047).

Before, nothing capped the agent stages a work item could run, and `attempts.default_max` /
`by_role` were read by no one — dead config shown in the policy editor.
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any

import pytest
from choregos_contracts import StageResult, StageStatus

from .conftest import Fixture
from .test_budget_reservation import _plan
from .test_interpreter import _runs, _wait_state, _wait_until, scripted, start

pytestmark = pytest.mark.asyncio

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"
FAILURE = StageResult(status=StageStatus.FAILED, summary="tests still red", reason="agent_error")


async def _attempts(setup: Fixture, **attempts: Any) -> None:
    """Sets fields of the active policy's `attempts`."""
    from choregos_api.db.models import PolicyDef
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        row = (
            await session.execute(
                select(PolicyDef).where(PolicyDef.project_id == setup.project_id, PolicyDef.is_active)
            )
        ).scalar_one()
        doc = json.loads(json.dumps(row.json_doc))
        doc.setdefault("attempts", {}).update(attempts)
        row.json_doc = doc


async def _past_runs(setup: Fixture, count: int) -> None:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        for n in range(count):
            session.add(
                Run(
                    id=f"run-past-{n}",
                    work_item_id=setup.work_item_id,
                    project_id=setup.project_id,
                    stage_role="implement",
                    status="failed",
                )
            )


async def test_the_51st_stage_is_refused_at_the_default_50(setup: Fixture) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    await _past_runs(setup, 49)
    fiftieth = await prepare_stage(_plan(setup, attempt=1))
    assert "refused" not in fiftieth, "the 50th stage runs"
    refused = await prepare_stage(_plan(setup, attempt=2))
    assert refused["refused"]["reason"] == "max_stages"
    assert "50" in refused["refused"]["detail"]
    # The key is minted when a run starts (#523): what prepare_stage holds is its reservation.
    from choregos_api.db.models import GatewayKeyRow
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        held = (await session.execute(select(GatewayKeyRow.key_id))).scalars().all()
    assert held == [f"reserved:{fiftieth['run_id']}"], "no key nor reservation for the refused stage"


async def test_a_capped_ticket_goes_to_a_person_instead_of_retrying(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    await _attempts(setup, max_stages_per_ticket=1)
    scripted(setup.adapters, FAILURE, FAILURE)
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "needs_human")
        await _wait_until(handle, "a person is asked", lambda s: s["pending_request"])
        assert await _runs(setup) == 1, "the retry would have been the second stage"
        await handle.signal("control", {"action": "stop"})
        await handle.result()
        history = await handle.fetch_history()
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if os.environ.get("CHOREGOS_ARCHIVER_HISTORIQUE"):
        target = HISTORIES / "wi-stage-cap-S27-08.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_the_policy_ceiling_lowers_the_workflow_attempts(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """`t-refine` allows 2 attempts; `by_role: {refine: 1}` escalates after the first failure."""
    await _attempts(setup, by_role={"refine": 1})
    scripted(setup.adapters, FAILURE, FAILURE)
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "needs_human")
        assert await _runs(setup) == 1
        await handle.signal("control", {"action": "stop"})
        await handle.result()
