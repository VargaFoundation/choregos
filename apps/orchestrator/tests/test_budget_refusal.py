"""A gateway budget refusal parks the work item after ONE run (S27-01, ADR 0047).

Before, the runner reported the refusal as `agent_silencieux` / `invalid_result`, and a workflow
without `on_budget_exceeded` retried the stage — with a fresh key at the full stage budget.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
from choregos_contracts import StageInput, StageResult

from .conftest import Fixture, recording_history, stage_result
from .test_interpreter import _runs, _wait_state, _wait_until, start

pytestmark = pytest.mark.asyncio

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"


def refusing_runner(gateway: Any) -> Any:
    """What a run does against a key the fake gateway caps: it spends, the gateway refuses, and the
    runner reports the refusal the way `choregos_runner` does."""
    from choregos_adapters.fakes import BudgetExceededError
    from choregos_runner.budget import BUDGET_REASON, gateway_budget_refusal
    from choregos_runner.result import fallback_result

    def run(stage_input: StageInput) -> StageResult:
        key_id = stage_input.gateway_key.removeprefix("sk-fake-")
        model = gateway.keys[key_id].models[0]
        try:
            for _ in range(10_000):  # the agent keeps calling the model until refused
                gateway.record_usage(key_id, tokens_in=100_000, tokens_out=10_000, model=model)
        except BudgetExceededError as refusal:
            seen = gateway_budget_refusal([str(refusal)])
            assert seen, f"the runner must recognise the gateway's words: {refusal}"
            return fallback_result(BUDGET_REASON, f"the gateway refused the run's key — {seen}")
        return stage_result("never refused")

    return run


async def install_workflow(setup: Fixture, *, drop: tuple[str, ...]) -> None:
    """`default-simple`, minus some of its `from_any_agent_state` defaults, pinned on the ticket."""
    from choregos_api.db.models import WorkflowDef, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_core import load_template

    wf = load_template("default-simple").model_copy(deep=True)
    assert wf.defaults is not None and wf.defaults.from_any_agent_state is not None
    for attr in drop:
        setattr(wf.defaults.from_any_agent_state, attr, None)
    wf.metadata.name = "budget-test"
    async with session_scope() as session:
        row = WorkflowDef(
            project_id=setup.project_id,
            name="budget-test",
            version=1,
            source="platform",
            yaml="",
            json_doc=wf.model_dump(mode="json", by_alias=True, exclude_none=True),
            checksum="sha256:budget-test",
            is_active=True,
        )
        session.add(row)
        await session.flush()
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.workflow_def_id = row.id


async def _state_reasons(setup: Fixture) -> list[str]:
    from choregos_api.db.models import Event
    from choregos_api.db.session import session_scope
    from choregos_contracts import EventType
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Event).where(
                    Event.work_item_id == setup.work_item_id,
                    Event.type == str(EventType.WORKITEM_STATE_CHANGED),
                )
            )
        ).scalars()
        return [str((row.payload or {}).get("reason", "")) for row in rows]


async def test_a_refusing_gateway_parks_the_ticket_after_one_run(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """The workflow declares no `on_budget_exceeded`: the ticket still goes to `needs_human`, with
    the reason `budget`, after a single run — no retry with a fresh key."""
    from temporalio.worker import Replayer

    await install_workflow(setup, drop=("on_budget_exceeded",))
    setup.adapters.executor.handler = refusing_runner(setup.adapters.gateway)
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "needs_human")
        await _wait_until(handle, "a person is asked", lambda s: s["pending_request"])
        assert await _runs(setup) == 1, "a budget refusal is never retried"
        await handle.signal("control", {"action": "stop"})
        await handle.result()
        history = await handle.fetch_history()

    reasons = await _state_reasons(setup)
    assert any(r.startswith("budget") for r in reasons), reasons
    assert len(setup.adapters.gateway.keys) + len(setup.adapters.gateway.revoked) == 1, "one key minted"

    from choregos_orchestrator.workflows import ALL_WORKFLOWS

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        target = HISTORIES / "wi-budget-refusal-S27-01.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_before_the_marker_a_budget_stop_was_retried(
    setup: Fixture, temporal_env: Any, worker_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A history recorded before `budget-never-retries` keeps its old decision on replay."""
    from choregos_orchestrator.workflows import interpreter

    real = interpreter.workflow.patched
    monkeypatch.setattr(
        interpreter.workflow,
        "patched",
        lambda marker: False if marker == interpreter.BUDGET_NEVER_RETRIES else real(marker),
    )
    await install_workflow(setup, drop=("on_budget_exceeded",))
    setup.adapters.executor.handler = refusing_runner(setup.adapters.gateway)
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "needs_human")
        assert await _runs(setup) == 2
        await handle.signal("control", {"action": "stop"})
        await handle.result()
