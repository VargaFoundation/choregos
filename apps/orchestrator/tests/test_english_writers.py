"""The writers of what histories and rows record write English (S45-39, ADR 0098 section 4).

S45-03 made every reader accept the English spelling next to the French one; one release later the
writers switch. A history recorded before still replays (`tests/replay`), a row written before is
still read; what is written from now on is English.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

# Fixtures and helpers of the scenes this file reuses, as the suites around it do.
from .conftest import Fixture
from .test_awaited_evidence import _action, _awaiting, _start, done  # noqa: F401
from .test_dated_action import _arrival, _wait_for, _work_item, done_effects  # noqa: F401
from .test_dated_action import _start as _start_interpreter


def _decoded(payload: Any) -> Any:
    return json.loads(payload.data)


def _sent(history: Any, activity: str) -> list[dict[str, Any]]:
    """The first payload of each call to `activity` in `history`."""
    from temporalio.api.enums.v1 import EventType

    return [
        _decoded(event.activity_task_scheduled_event_attributes.input.payloads[0])
        for event in history.events
        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
        and event.activity_task_scheduled_event_attributes.activity_type.name == activity
    ]


async def test_activities_raise_english_types(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unknown agent raises `AgentUnavailable`, and the interpreter still treats it (and the
    French spelling a history recorded before) as a refusal of configuration."""
    from choregos_api.services import agents
    from choregos_orchestrator.activities import actions
    from choregos_orchestrator.activities.remote import refuse
    from choregos_orchestrator.activities.stage import _plan_agent
    from choregos_orchestrator.workflows.interpreter_parts.helpers import CONFIGURATION_REFUSALS
    from temporalio.exceptions import ApplicationError

    async def unknown(*_: Any, **__: Any) -> Any:
        raise agents.AgentUnavailable("the agent `ghost` does not exist")

    monkeypatch.setattr(agents, "resolve_agent", unknown)
    bundle = SimpleNamespace(project=SimpleNamespace(org_id="org", id="project"))
    plan = SimpleNamespace(agent="ghost", work_item_id=None)
    with pytest.raises(ApplicationError) as raised:
        await _plan_agent(None, bundle, plan)
    assert raised.value.type == "AgentUnavailable" and raised.value.non_retryable
    assert refuse("x").type == "AgentUnavailable"
    assert {raised.value.type, "AgentIndisponible"} <= CONFIGURATION_REFUSALS  # lang: fr-ok - read forever

    class Empty:
        async def get(self, *_: Any) -> None:
            return None

    with pytest.raises(ApplicationError) as missing:
        await actions._load(Empty(), "a-1")
    assert missing.value.type == "ActionNotFound" == actions.ACTION_NOT_FOUND
    assert (actions.ACTION_NOT_VISIBLE_YET, actions.EFFECT_REFUSED) == (
        "ActionNotYetVisible",
        "EffectRefused",
    )


async def test_new_runs_send_english_keys(
    setup: Fixture,
    temporal_env: Any,
    worker_factory: Any,
    done: list[Any],  # noqa: F811
) -> None:
    """A fresh `ActionWorkflow` sends `{waiting, until}` then `{waiting, evidence}`, and what is
    persisted in the action's result is English too."""
    from datetime import timedelta

    until = (await temporal_env.get_current_time() + timedelta(hours=1)).isoformat()
    action_id = await _action(setup, until)
    async with worker_factory():
        handle = await _start(temporal_env, action_id)
        row = await _awaiting(action_id)
        assert row.result["waiting"] == {"position": 1, "until": until}
        await handle.signal("evidence", {"position": 1, "ok": True, "detail": "the key is gone"})
        await handle.result()
        history = await handle.fetch_history()
    first, second = _sent(history, "mark_awaiting_evidence")
    assert {key for key in first if key != "action_id"} == {"position", "waiting", "until"}
    assert first["waiting"] is True and first["until"] == until
    assert {key for key in second if key != "action_id"} == {"position", "waiting", "evidence"}
    assert second["waiting"] is False and second["evidence"]["detail"] == "the key is gone"
    assert _sent(history, "load_action") == [action_id]


async def test_proposed_action_refusal_is_english(
    setup: Fixture,
    temporal_env: Any,
    worker_factory: Any,
    done_effects: list[dict[str, Any]],  # noqa: F811
) -> None:
    """`propose_transition_action` returns `{refusal: ...}`, and the interpreter fails the transition
    on it (`action_succeeded`, with the refusal as its detail)."""
    from choregos_orchestrator.activities.actions import propose_transition_action

    start = await temporal_env.get_current_time()
    fields = {"upn": "lea@acme.test", "arrival_date": start.isoformat()}
    document = _arrival(params={"upn": "{{ fields.employee_number }}"})
    await _work_item(setup, document, fields)

    answer = await propose_transition_action(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "transition": "t-accounts",
            "attempt": 0,
            "workflow_id": "wi-test",
            "action": document["transitions"][0]["action"],
        }
    )
    assert set(answer) == {"refusal"} and "employee_number" in answer["refusal"]

    async with worker_factory():
        handle = await _start_interpreter(temporal_env, setup)
        status = await _wait_for(handle, lambda s: s["state"] == "to_review", "the work item is to review")
        await handle.signal("control", {"action": "stop"})
    assert status["attempts"] == {"t-accounts": 1}
    # The interpreter read `refusal` and failed the transition: no action was ever proposed.
    from choregos_api.db.models import Event
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    # The event is written by an activity that may end after the state query saw `to_review` (on
    # PostgreSQL the read came back empty once, run 41882317486): read until it is there, ten seconds at most.
    reasons: list[str] = []
    for _ in range(100):
        async with session_scope() as session:
            rows = (
                await session.execute(select(Event).where(Event.work_item_id == setup.work_item_id))
            ).scalars()
            reasons = [str((e.payload or {}).get("reason")) for e in rows]
        if reasons:
            break
        await asyncio.sleep(0.1)
    assert reasons == ["failing gates: action_succeeded"], reasons
    assert done_effects == []
