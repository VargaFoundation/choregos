"""`migrate`: the ticket changes definition without dying (ADR 0031, S16-05).

The API forwarded the target's identifier, and the interpreter validated the control message
ITSELF as a definition: `Workflow.model_validate` raised in the workflow code, and the
ticket died. The definition now arrives in the signal, read by the API; the interpreter
remaps the state, records the migration, and the ticket's pin follows: a `continue_as_new` will reload
the target, not the old one. A migration that became impossible leaves the ticket alive, on its definition.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from typing import Any

import pytest

from .conftest import Fixture, recording_history

pytestmark = pytest.mark.asyncio

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"

#: A parked ticket: a waiting state with no outgoing transition, which only the board would move.
PARKING: dict[str, Any] = {
    "apiVersion": "choregos/v1",
    "kind": "Workflow",
    "metadata": {"name": "parking", "version": 1},
    "initial": "parked",
    "actors": {"hr": {"type": "human", "group": "hr"}},
    "states": {
        "parked": {"display": "Parked", "kind": "wait"},
        "done": {"display": "Done", "terminal": True},
    },
    "transitions": [],
}
ARRIVAL: dict[str, Any] = {
    "apiVersion": "choregos/v1",
    "kind": "Workflow",
    "metadata": {"name": "onboarding", "version": 1},
    "initial": "request",
    "actors": {"platform": {"type": "system"}},
    "states": {
        "request": {"display": "Request", "kind": "wait"},
        "done": {"display": "Done", "terminal": True},
    },
    "transitions": [{"id": "t-do", "from": "request", "to": "done", "by": "platform"}],
}


async def _define(setup: Fixture, document: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """A definition in the database, and what the API would send of it in the signal."""
    from choregos_api.db.models import WorkflowDef
    from choregos_api.db.session import session_scope
    from choregos_contracts import Workflow

    model = Workflow.model_validate(document)
    signal = model.model_dump(mode="json", by_alias=True, exclude_none=True)
    async with session_scope() as session:
        row = WorkflowDef(
            project_id=setup.project_id,
            name=model.metadata.name,
            version=model.metadata.version,
            source="platform",
            yaml="",
            json_doc=signal,
            checksum=f"sha256:{model.metadata.name}",
            is_active=True,
        )
        session.add(row)
        await session.flush()
        return row.id, signal


async def _park(setup: Fixture) -> str:
    """The ticket is born in `parking`, in state `parked`."""
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    parking, _ = await _define(setup, PARKING)
    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.workflow_def_id, item.state = parking, "parked"
    return parking


async def _start(env: Any, setup: Fixture) -> Any:
    return await env.client.start_workflow(
        "WorkflowInterpreter",
        {
            "project_id": setup.project_id,
            "project_slug": setup.project_slug,
            "work_item_id": setup.work_item_id,
            "tracker_key": setup.tracker_key,
        },
        id=f"wi-{setup.project_slug}-migration",
        task_queue="test",
    )


async def _wait_for(handle: Any, predicate: Any, what: str) -> dict[str, Any]:
    for _ in range(600):
        status = await handle.query("status")
        if predicate(status):
            return dict(status)
        await asyncio.sleep(0.1)
    raise AssertionError(f"{what}: never true (status: {await handle.query('status')})")


async def _events(setup: Fixture) -> list[Any]:
    from choregos_api.db.models import Event
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = await session.execute(
            select(Event).where(Event.work_item_id == setup.work_item_id, Event.type.like("%migrat%"))
        )
        return list(rows.scalars())


async def test_a_parked_ticket_migrates_and_ends_on_its_new_definition(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    await _park(setup)
    arrival, signal = await _define(setup, ARRIVAL)
    async with worker_factory():
        handle = await _start(temporal_env, setup)
        await _wait_for(handle, lambda s: s["state"] == "parked", "the ticket is parked")
        await handle.signal(
            "control",
            {
                "action": "migrate",
                "workflow_def_id": arrival,
                "workflow": signal,
                "state_mapping": {"parked": "request"},
                "by": "admin@varga.dev",
            },
        )
        # Without a wake-up, a parked ticket would never have migrated: the test says so, not waits.
        result = await asyncio.wait_for(handle.result(), timeout=120)
        history = await handle.fetch_history()

    assert result["state"] == "done", "the system transition of the NEW definition played"
    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        assert item.workflow_def_id == arrival, "the pin follows: a continue_as_new would reload the target"
        assert item.failure is None
    (migration,) = await _events(setup)
    assert migration.type == "choregos.workitem.migrated"
    assert (migration.payload["from_state"], migration.payload["to_state"]) == ("parked", "request")

    # The new history replays against the current code, and is archived for those to come.
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        target = HISTORIES / "wi-migration-S16-05.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_an_impossible_migration_leaves_the_ticket_alive(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    parking = await _park(setup)
    arrival, signal = await _define(setup, ARRIVAL)
    async with worker_factory():
        handle = await _start(temporal_env, setup)
        await _wait_for(handle, lambda s: s["state"] == "parked", "the ticket is parked")
        # No mapping: `parked` does not exist in `onboarding`.
        await handle.signal("control", {"action": "migrate", "workflow_def_id": arrival, "workflow": signal})
        for _ in range(600):
            if await _events(setup):
                break
            await asyncio.sleep(0.1)
        status = await handle.query("status")
        assert (status["state"], status["stopped"]) == ("parked", False), (
            "the ticket lives, on its definition"
        )
        await handle.signal("control", {"action": "stop"})
        result = await handle.result()

    assert result["state"] == "parked"
    (refusal,) = await _events(setup)
    assert refusal.type == "choregos.workitem.migration_refused"
    assert "parked" in refusal.payload["reason"]
    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        assert item.workflow_def_id == parking and item.failure is None


async def test_recording_the_same_migration_twice_does_not_double_it(setup: Fixture) -> None:
    """A replayed activity (ADR 0008): the migration's key is in the event."""
    from choregos_orchestrator.activities.interpretation import record_migration

    arrival, _ = await _define(setup, ARRIVAL)
    recorded_migration = {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "key": "wi-x/run-1/1",
        "from_state": "inbox",
        "to_state": "request",
        "workflow_def_id": arrival,
    }
    assert (await record_migration(recorded_migration))["recorded"] is True
    assert (await record_migration(recorded_migration))["recorded"] is False
    assert len(await _events(setup)) == 1
