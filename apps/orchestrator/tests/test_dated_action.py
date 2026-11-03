"""A system transition proposes a governed action, on a date (S20-05) — in accelerated time.

The work item waits until D-10 before its arrival date; before that, nothing is proposed. Moving the
date re-arms the timer; moving it earlier than the present time sends the action at once. The action
is played in its own `ActionWorkflow`, never in the interpreter, which learns of it through
`action_settled`. Rejected or failed, it follows `on_fail`; impossible to propose, it says so.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest

from .conftest import Fixture, recording_history

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"


def _arrival(policy: str = "allowed", params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "apiVersion": "choregos/v1",
        "kind": "Workflow",
        "metadata": {
            "name": "onboarding",
            "version": 1,
            "inputs": {
                "type": "object",
                "properties": {
                    "upn": {"type": "string"},
                    "arrival_date": {"type": "string", "format": "date-time"},
                },
            },
        },
        "initial": "prepared",
        "actors": {"platform": {"type": "system"}},
        "states": {
            "prepared": {"display": "Prepared", "kind": "wait"},
            "to_review": {"display": "To review", "kind": "wait"},
            "done": {"display": "Done", "terminal": True},
        },
        "transitions": [
            {
                "id": "t-accounts",
                "from": "prepared",
                "to": "done",
                "by": "platform",
                "action": {
                    "kind": "arrival.accounts",
                    "title": "The accounts of {{ fields.upn }}",
                    "params": params or {"upn": "{{ fields.upn }}"},
                    "effects": [{"effect": f"test.create.{policy}", "with": {"upn": "{{ params.upn }}"}}],
                    "not_before": "fields.arrival_date - 10d",
                },
                "on_fail": {"to": "prepared", "max_attempts": 1, "escalate_to": "to_review"},
            }
        ],
    }


@pytest.fixture
def done_effects() -> Iterator[list[dict[str, Any]]]:
    """What the effects REALLY did: the truth of the outside world."""
    from choregos_api import effects

    seen: list[dict[str, Any]] = []

    async def create(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        seen.append(params)
        return {"id": f"account-{params['upn']}"}

    effects.declare_effect("test.create.allowed", create, policy="allowed")
    effects.declare_effect("test.create.approval", create, policy="approval")
    yield seen
    effects.reset()


async def _work_item(setup: Fixture, document: dict[str, Any], fields: dict[str, Any]) -> None:
    from choregos_api.db.models import WorkflowDef, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_contracts import Workflow

    model = Workflow.model_validate(document)
    async with session_scope() as session:
        row = WorkflowDef(
            project_id=setup.project_id,
            name="onboarding",
            version=1,
            source="platform",
            yaml="",
            json_doc=model.model_dump(mode="json", by_alias=True, exclude_none=True),
            checksum="sha256:onboarding",
            is_active=True,
        )
        session.add(row)
        await session.flush()
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.workflow_def_id, item.state, item.fields = row.id, "prepared", fields


async def _start(env: Any, setup: Fixture) -> Any:
    return await env.client.start_workflow(
        "WorkflowInterpreter",
        {
            "project_id": setup.project_id,
            "project_slug": setup.project_slug,
            "work_item_id": setup.work_item_id,
            "tracker_key": setup.tracker_key,
        },
        id=f"wi-{setup.project_slug}-varga_billing-api-123",
        task_queue="test",
    )


async def _wait_for(handle: Any, predicate: Any, what: str) -> dict[str, Any]:
    for _ in range(600):
        status = await handle.query("status")
        if predicate(status):
            return dict(status)
        await asyncio.sleep(0.05)
    raise AssertionError(f"{what}: never true (status: {await handle.query('status')})")


async def _actions(setup: Fixture) -> list[Any]:
    from choregos_api.db.models import Action
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = await session.execute(select(Action).where(Action.work_item_id == setup.work_item_id))
        return list(rows.scalars())


def _iso(instant: datetime) -> str:
    return instant.isoformat()


async def test_the_action_waits_for_d_minus_10_then_goes_and_the_work_item_learns_it_by_a_signal(
    setup: Fixture, temporal_env: Any, worker_factory: Any, done_effects: list[dict[str, Any]]
) -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.api.enums.v1 import EventType
    from temporalio.worker import Replayer

    start = await temporal_env.get_current_time()
    arrival = start + timedelta(days=20)
    await _work_item(setup, _arrival(), {"upn": "lea@acme.test", "arrival_date": _iso(arrival)})
    async with worker_factory():
        handle = await _start(temporal_env, setup)
        status = await _wait_for(handle, lambda s: s.get("waiting_until"), "the work item waits for its date")
        assert datetime.fromisoformat(status["waiting_until"]) == arrival - timedelta(days=10)
        await temporal_env.sleep(timedelta(days=9, hours=23))
        assert await _actions(setup) == [], "nothing is proposed before D-10"
        assert done_effects == []
        await temporal_env.sleep(timedelta(hours=2))
        result = await handle.result()
        history = await handle.fetch_history()
    assert result["state"] == "done"
    assert done_effects == [{"upn": "lea@acme.test"}], "the effect was played once"
    (action,) = await _actions(setup)
    assert (action.origin, action.status, action.title) == (
        "transition",
        "succeeded",
        "The accounts of lea@acme.test",
    )
    assert action.decisions[0]["by"] == "policy", "every operation is allowed: the policy decided"
    assert action.proposed_by["transition"] == "t-accounts"
    signals = [
        e.workflow_execution_signaled_event_attributes.signal_name
        for e in history.events
        if e.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_SIGNALED
    ]
    assert "action_settled" in signals, (
        "the ActionWorkflow warns the work item: it does not wait for a re-read"
    )
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        english = recording_history() == "english"  # S34-02
        target = HISTORIES / ("wi-action-english-S34-02.json" if english else "wi-dated-action-S20-05.json")
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_moving_the_date_re_arms_the_timer_and_moving_it_into_the_past_sends_the_action(
    setup: Fixture, temporal_env: Any, worker_factory: Any, done_effects: list[dict[str, Any]]
) -> None:
    start = await temporal_env.get_current_time()
    fields = {"upn": "lea@acme.test", "arrival_date": _iso(start + timedelta(days=20))}
    await _work_item(setup, _arrival(), fields)
    async with worker_factory():
        handle = await _start(temporal_env, setup)
        await _wait_for(handle, lambda s: s.get("waiting_until"), "the work item waits for its date")
        await temporal_env.sleep(timedelta(days=5))
        later = start + timedelta(days=30)
        await handle.signal("fields_changed", {"fields": {**fields, "arrival_date": _iso(later)}})
        await _wait_for(
            handle,
            lambda s: s.get("waiting_until") == _iso(later - timedelta(days=10)),
            "the timer is re-armed on the new date",
        )
        await temporal_env.sleep(timedelta(days=7))  # D+12: the old deadline (D+10) has passed
        assert await _actions(setup) == [] and done_effects == [], "the old date no longer triggers anything"
        sooner = start + timedelta(days=15)  # D-10 falls on D+5: already past
        await handle.signal("fields_changed", {"fields": {**fields, "arrival_date": _iso(sooner)}})
        # Without advancing the clock: the past date sends the action right away, it does not wait
        # for the old timer.
        await _wait_for(handle, lambda s: s["state"] == "done", "the action goes at once")
        result = await handle.result()
    assert result["state"] == "done"
    assert done_effects == [{"upn": "lea@acme.test"}]


async def test_a_paused_work_item_does_not_go_on_its_date_it_goes_on_resume(
    setup: Fixture, temporal_env: Any, worker_factory: Any, done_effects: list[dict[str, Any]]
) -> None:
    start = await temporal_env.get_current_time()
    fields = {"upn": "lea@acme.test", "arrival_date": _iso(start + timedelta(days=12))}
    await _work_item(setup, _arrival(), fields)
    async with worker_factory():
        handle = await _start(temporal_env, setup)
        await _wait_for(handle, lambda s: s.get("waiting_until"), "the work item waits for its date")
        await handle.signal("control", {"action": "pause"})
        await _wait_for(handle, lambda s: s["paused"], "the work item is paused")
        await temporal_env.sleep(timedelta(days=3))  # D-10 has passed
        assert await _actions(setup) == [] and done_effects == [], "paused, nothing goes"
        await handle.signal("control", {"action": "resume"})
        await _wait_for(handle, lambda s: s["state"] == "done", "the resume sends the action")
    assert done_effects == [{"upn": "lea@acme.test"}]


async def test_a_lost_signal_the_re_read_takes_over(
    setup: Fixture,
    temporal_env: Any,
    worker_factory: Any,
    done_effects: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`action_settled` never arrives (a work item unreachable for a moment): the re-read from the
    database, every six hours, settles the wait anyway."""
    from choregos_orchestrator.activities import actions

    async def lost(*_: Any) -> None:
        return None

    monkeypatch.setattr(actions, "_notify", lost)
    start = await temporal_env.get_current_time()
    await _work_item(setup, _arrival(), {"upn": "lea@acme.test", "arrival_date": _iso(start)})
    async with worker_factory():
        handle = await _start(temporal_env, setup)
        result = await handle.result()
    assert result["state"] == "done" and done_effects == [{"upn": "lea@acme.test"}]


async def test_a_rejected_action_is_not_played_and_the_work_item_follows_on_fail(
    setup: Fixture, temporal_env: Any, worker_factory: Any, done_effects: list[dict[str, Any]]
) -> None:
    """An operation under approval: the action waits for a person. Rejected — what `decider` does
    in the API: the row, then `action_settled` —, nothing is played, and the work item goes where
    `on_fail` sends it."""
    from choregos_api.db.models import Action
    from choregos_api.db.session import session_scope

    start = await temporal_env.get_current_time()
    fields = {"upn": "lea@acme.test", "arrival_date": _iso(start + timedelta(days=5))}
    await _work_item(setup, _arrival(policy="approval"), fields)
    async with worker_factory():
        handle = await _start(temporal_env, setup)
        status = await _wait_for(handle, lambda s: s.get("action"), "the action is proposed")
        (action,) = await _actions(setup)
        assert (action.id, action.status, action.decisions) == (status["action"], "pending_approval", [])
        async with session_scope() as session:
            row = await session.get(Action, action.id)
            assert row is not None
            row.status = "rejected"
        await handle.signal("action_settled", {"action_id": action.id, "status": "rejected"})
        await _wait_for(handle, lambda s: s["state"] == "to_review", "the work item is to review")
        await handle.signal("control", {"action": "stop"})
    assert done_effects == [], "rejected: no effect"


async def test_an_action_impossible_to_propose_says_so_and_the_work_item_follows_on_fail(
    setup: Fixture, temporal_env: Any, worker_factory: Any, done_effects: list[dict[str, Any]]
) -> None:
    start = await temporal_env.get_current_time()
    fields = {"upn": "lea@acme.test", "arrival_date": _iso(start)}
    await _work_item(setup, _arrival(params={"upn": "{{ fields.employee_number }}"}), fields)
    async with worker_factory():
        handle = await _start(temporal_env, setup)
        await _wait_for(handle, lambda s: s["state"] == "to_review", "the work item is to review")
        await handle.signal("control", {"action": "stop"})
    assert await _actions(setup) == [] and done_effects == []


async def test_an_action_written_with_verifier_still_runs(
    setup: Fixture, temporal_env: Any, worker_factory: Any, done_effects: list[dict[str, Any]]
) -> None:
    """S39-11a: `verifier` is the old name of the `check` effect; an action stored with it, after
    an effect that did something, still completes (the alias is guarded by this case)."""
    start = await temporal_env.get_current_time()
    document = _arrival()
    effect_list = document["transitions"][0]["action"]["effects"]
    check = {"condition": "{{ effects[0].id is not none }}", "motif": "the account"}  # lang: fr-ok
    effect_list.append({"effect": "verifier", "with": check})  # lang: fr-ok
    await _work_item(setup, document, {"upn": "lea@acme.test", "arrival_date": _iso(start)})
    async with worker_factory():
        handle = await _start(temporal_env, setup)
        result = await handle.result()
    assert result["state"] == "done" and done_effects == [{"upn": "lea@acme.test"}]
    (action,) = await _actions(setup)
    assert action.status == "succeeded"
