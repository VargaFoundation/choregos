"""An action awaits the EVIDENCE that one of its effects served (S20-08): a report to come, an outside
event. The effect asks for it (`await_evidence`), the workflow awaits the `preuve` signal until the
deadline: favourable, the action succeeds; contrary or absent, it fails, and what was done
is compensated, in reverse, as for a refusal.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest

from .conftest import Fixture, recording_history

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"


@pytest.fixture
def done() -> Iterator[list[tuple[str, dict[str, Any]]]]:
    from choregos_api import effects

    seen: list[tuple[str, dict[str, Any]]] = []

    async def create(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        seen.append(("create", params))
        return {"id": "correctif-1"}

    async def wait(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        seen.append(("wait", params))
        return {"await_evidence": {"until": params["until"]}}

    async def undo(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        seen.append(("undo", params))
        return {}

    for name, function in (("test.create", create), ("test.wait", wait), ("test.undo", undo)):
        effects.declare_effect(name, function)
    yield seen
    effects.reset()


async def _action(setup: Fixture, until: str) -> str:
    from choregos_api.db.models import Action, Project
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        action = Action(
            org_id=project.org_id,
            project_id=project.id,
            origin="ontology",
            kind="ontology.verify_finding_fixed",
            title="The fix, and the evidence that it served",
            params={"until": until},
            effects=[
                {"effect": "test.create", "with": {}, "compensate": {"effect": "test.undo", "with": {}}},
                {"effect": "test.wait", "with": {"until": "{{ params.until }}"}},
            ],
            proposed_by={"kind": "system", "id": "system"},
            approval={},
            decisions=[],
            status="approved",
        )
        session.add(action)
        await session.flush()
        return str(action.id)


async def _row(action_id: str) -> Any:
    from choregos_api.db.models import Action
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        return await session.get(Action, action_id)


async def _start(temporal_env: Any, action_id: str) -> Any:
    return await temporal_env.client.start_workflow(
        "ActionWorkflow", {"action_id": action_id}, id=f"action-{action_id}", task_queue="test"
    )


async def _awaiting(action_id: str) -> Any:
    for _ in range(400):
        row = await _row(action_id)
        if row.status == "awaiting_evidence":
            return row
        await asyncio.sleep(0.05)
    raise AssertionError(f"the action never awaits its evidence ({(await _row(action_id)).status})")


async def test_an_action_awaits_its_evidence_and_succeeds_when_it_comes(
    setup: Fixture, temporal_env: Any, worker_factory: Any, done: list[Any]
) -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    until = (await temporal_env.get_current_time() + timedelta(hours=1)).isoformat()
    action_id = await _action(setup, until)
    async with worker_factory():
        handle = await _start(temporal_env, action_id)
        row = await _awaiting(action_id)
        assert row.result["attente"] == {"position": 1, "jusqu_a": until}  # lang: fr-ok (recorded wire value)
        await handle.signal(
            "preuve", {"position": 1, "ok": True, "detail": "the key is absent from the next report"}
        )
        result = await handle.result()
        history = await handle.fetch_history()
    assert result["status"] == "succeeded"
    row = await _row(action_id)
    assert row.status == "succeeded" and "attente" not in row.result
    assert row.result["preuves"][0]["detail"] == "the key is absent from the next report"
    assert [name for name, _ in done] == ["create", "wait"], "nothing is undone"
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        target = HISTORIES / "action-evidence-S20-08.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_contrary_evidence_fails_the_action_and_compensates(
    setup: Fixture, temporal_env: Any, worker_factory: Any, done: list[Any]
) -> None:
    until = (await temporal_env.get_current_time() + timedelta(hours=1)).isoformat()
    action_id = await _action(setup, until)
    async with worker_factory():
        handle = await _start(temporal_env, action_id)
        await _awaiting(action_id)
        await handle.signal("preuve", {"position": 1, "ok": False, "detail": "the key is still there"})
        result = await handle.result()
    assert result["status"] == "failed" and "the evidence says no: the key is still there" in result["error"]
    assert [name for name, _ in done] == ["create", "wait", "undo"], "what was done is undone"
    assert (await _row(action_id)).status == "failed"


async def test_without_evidence_before_the_deadline_the_action_fails(
    setup: Fixture, temporal_env: Any, worker_factory: Any, done: list[Any]
) -> None:
    until = (await temporal_env.get_current_time() + timedelta(minutes=15)).isoformat()
    action_id = await _action(setup, until)
    async with worker_factory():
        handle = await _start(temporal_env, action_id)
        await _awaiting(action_id)
        result = await handle.result()  # time skips to the deadline
    assert result["status"] == "failed" and "timed out" in result["error"]
    assert [name for name, _ in done][-1] == "undo"


async def test_the_evidence_signal_and_english_activity_names(
    setup: Fixture, temporal_env: Any, worker_factory: Any, done: list[Any]
) -> None:
    """S34-02: a new action schedules the English activity names, takes the `evidence` signal, and
    its history replays. Archived as the English counterpart of `action-evidence-S20-08`."""
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.api.enums.v1 import EventType
    from temporalio.worker import Replayer

    until = (await temporal_env.get_current_time() + timedelta(hours=1)).isoformat()
    action_id = await _action(setup, until)
    async with worker_factory():
        handle = await _start(temporal_env, action_id)
        await _awaiting(action_id)
        await handle.signal("evidence", {"position": 1, "ok": True, "detail": "the key is gone"})
        result = await handle.result()
        history = await handle.fetch_history()
    assert result["status"] == "succeeded"
    activities = [
        e.activity_task_scheduled_event_attributes.activity_type.name
        for e in history.events
        if e.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
    ]
    assert activities == [
        "load_action",
        "execute_effect",
        "execute_effect",
        "mark_awaiting_evidence",
        "mark_awaiting_evidence",
        "close_action",
    ], activities
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        target = HISTORIES / "action-evidence-english-S34-02.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")
