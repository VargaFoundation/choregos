"""S35-03 — an answered question costs ONE run with an executor that can suspend (ADR 0016).

Without `suspend`/`resume`, the answer replays the stage: a second run, from scratch
(`test_reprise_apres_escalade.test_une_reponse_reprend_l_etape_qui_a_pose_la_question`, two
runs). With them, the run's sandbox is suspended while the person answers, and THAT run goes on
with the answer. Past `sandbox.suspend_max_hours`, the sandbox is destroyed and the answer
replays the stage as before. Played on the fakes, in skipped time; the history of the first
scenario is archived for `tests/replay`.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
from datetime import timedelta
from typing import Any

import pytest
from choregos_contracts import StageResult, StageStatus

from .conftest import Fixture, stage_result
from .test_interpreter import _runs, _wait_state, _wait_until, scripted, start
from .test_reprise_apres_escalade import _demandes

pytestmark = pytest.mark.asyncio

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"

QUESTION = StageResult(
    status=StageStatus.NEEDS_HUMAN,
    summary="which currency?",
    reason="question",
    questions=[{"text": "Which currency for credit notes?", "options": ["EUR", "order currency"]}],
)


def _can_suspend(setup: Fixture) -> Any:
    executor = setup.adapters.executor
    executor.capabilities = frozenset({"suspend", "resume"})
    return executor


async def _answer(handle: Any, setup: Fixture) -> None:
    asked = next(d for d in await _demandes(setup) if d.kind == "question")
    await handle.signal(
        "human_decision",
        {
            "request_id": asked.id,
            "kind": "question",
            "approved": False,
            "answer": "the order currency",
            "decided_by": "vincent",
        },
    )


async def _run_row(setup: Fixture) -> Any:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        (run,) = (await session.execute(select(Run).where(Run.work_item_id == setup.work_item_id))).scalars()
        session.expunge(run)
        return run


async def test_an_answered_question_costs_one_run_not_two(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    executor = _can_suspend(setup)
    scripted(setup.adapters, QUESTION, stage_result("spec"))
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "needs_human")
        await _wait_until(handle, "the question is asked", lambda s: s["pending_request"])
        (first,) = [spec.run_id for spec in executor.started]
        assert [run for run, _ in executor.suspended] == [first], "the sandbox waits, suspended"

        await _answer(handle, setup)
        await _wait_state(handle, "awaiting_spec_approval")
        await handle.signal("control", {"action": "stop"})
        await handle.result()
        history = await handle.fetch_history()

    assert await _runs(setup) == 1, "the answer resumed the run instead of starting a second one"
    assert executor.resumed == [first]
    assert len(executor.started) == 1, "no second start, no second sandbox"
    run = await _run_row(setup)
    prompt = run.stage_input["playbook"]["prompt"]
    assert "Which currency for credit notes?" in prompt and "the order currency" in prompt
    assert run.tokens["runs"] == 1 and run.tokens["resumed"] == 1
    item_runs = (await _work_item_totals(setup)).get("runs")
    assert item_runs == 1, "the work item counts one run"
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if os.environ.get("CHOREGOS_ARCHIVER_HISTORIQUE"):
        target = HISTORIES / "wi-suspend-around-human-S35-03.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_past_the_bound_the_sandbox_is_gone_and_the_stage_replays(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    executor = _can_suspend(setup)
    scripted(setup.adapters, QUESTION, stage_result("spec"))
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "needs_human")
        await _wait_until(handle, "the question is asked", lambda s: s["pending_request"])
        (first,) = [spec.run_id for spec in executor.started]
        # The default policy bound is 24 hours; the person answers on the second day.
        await temporal_env.sleep(timedelta(hours=25))
        # Watched on the executor, not by querying the workflow: a query right after a day of
        # skipped timers can outlast its deadline on a loaded CI runner.
        for _ in range(600):
            if first in executor.cancelled:
                break
            await asyncio.sleep(0.1)
        assert first in executor.cancelled, "past the bound, the suspended sandbox is released"

        await _answer(handle, setup)
        await _wait_state(handle, "awaiting_spec_approval")
        await handle.signal("control", {"action": "stop"})
        await handle.result()

    assert executor.resumed == []
    assert await _runs(setup) == 2, "past the bound, the answer replays the stage: a second run"


async def test_without_the_capability_nothing_is_suspended(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """Every other executor: the interpreter asks, is told no, and does nothing new."""
    executor = setup.adapters.executor
    scripted(setup.adapters, QUESTION, stage_result("spec"))
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "needs_human")
        await _wait_until(handle, "the question is asked", lambda s: s["pending_request"])
        await _answer(handle, setup)
        await _wait_state(handle, "awaiting_spec_approval")
        await handle.signal("control", {"action": "stop"})
        await handle.result()
    assert executor.suspended == [] and executor.resumed == []
    assert await _runs(setup) == 2


async def _work_item_totals(setup: Fixture) -> dict[str, Any]:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        return dict(item.totals or {})
