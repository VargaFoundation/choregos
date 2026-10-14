"""`AgentTrial` (S32-03): a draft agent version runs once, on a sample input, under a capped budget.

The draft stands in for a registry agent without being published; the run's gateway key is minted
at the trial's cap; the run counts in no registry agent's metrics; nothing of the interpreter runs.
"""

from __future__ import annotations

from typing import Any

import pytest

from .conftest import Fixture, stage_result

pytestmark = pytest.mark.asyncio

DRAFT = {
    "instructions": "Sort « {{ ticket.title }} » by urgency, then say why.",
    "model": "profile:cheap",
    "limits": {"max_turns": 6, "max_minutes": 10},
    "budget": {"run_usd": 2.0},
}


async def test_a_draft_runs_once_under_the_trial_s_cap(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    from choregos_api.db.models import GatewayKeyRow, Run
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    setup.adapters.executor.queue_result(stage_result("sorted: urgent"))
    async with worker_factory():
        outcome = await temporal_env.client.execute_workflow(
            "AgentTrial",
            {
                "project_id": setup.project_id,
                "work_item_id": setup.work_item_id,
                "spec": DRAFT,
                "budget_usd": 0.3,
            },
            id=f"trial-{setup.work_item_id}",
            task_queue="test",
        )
    assert outcome["status"] == "done", outcome
    assert outcome["summary"] == "sorted: urgent"
    assert outcome["budget_usd"] == pytest.approx(0.3), "the trial's cap tightens the version's 2 $"
    async with session_scope() as session:
        run = await session.get(Run, outcome["run_id"])
        assert run is not None
        assert run.stage_role == "implement" and run.transition_id == "trial"
        assert run.agent_slug is None, "a trial is no registry agent's run"
        assert run.status == "succeeded"
        prompt = run.stage_input["playbook"]["prompt"]
        assert "by urgency, then say why" in prompt, "the draft's instructions replace the playbook"
        assert run.stage_input["budget"]["max_turns"] == 6
        cle = (
            await session.execute(select(GatewayKeyRow).where(GatewayKeyRow.run_id == run.id))
        ).scalar_one()
        assert cle.budget_usd == pytest.approx(0.3), "the gateway key is minted at the trial's cap"


async def test_a_draft_s_day_budget_does_not_bound_its_trial(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """A trial's runs carry no agent, so no day spend counts against the draft's day budget: the
    trial's own cap bounds the key, not a day budget that would cap a run and never be spent."""
    from choregos_api.db.models import GatewayKeyRow
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    setup.adapters.executor.queue_result(stage_result("sorted"))
    draft = {**DRAFT, "budget": {"run_usd": 2.0, "daily_usd": 0.1}}
    async with worker_factory():
        outcome = await temporal_env.client.execute_workflow(
            "AgentTrial",
            {
                "project_id": setup.project_id,
                "work_item_id": setup.work_item_id,
                "spec": draft,
                "budget_usd": 0.3,
            },
            id=f"trial-day-{setup.work_item_id}",
            task_queue="test",
        )
    assert outcome["status"] == "done", outcome
    async with session_scope() as session:
        cle = (
            await session.execute(select(GatewayKeyRow).where(GatewayKeyRow.run_id == outcome["run_id"]))
        ).scalar_one()
    assert cle.budget_usd == pytest.approx(0.3), "the draft's 0.10 $ a day does not cap a trial"
