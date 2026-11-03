"""A human task, then the action that depends on it (S20-06): the person at reception hands over the badge
and enters its UID (the task), and the UID arrives in the parameters of `activate_badge`, which the
platform plays on the readers (the fake of S20-04) with the connector's key."""

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
ATTESTED = "I handed the badge over in person to its holder"
FORM = {"type": "object", "required": ["badge_uid"], "properties": {"badge_uid": {"type": "string"}}}
BADGE: dict[str, Any] = {
    "apiVersion": "choregos/v1",
    "kind": "Workflow",
    "metadata": {
        "name": "onboarding",
        "version": 1,
        "inputs": {
            "type": "object",
            "properties": {"upn": {"type": "string"}, "badge_uid": {"type": "string"}},
        },
    },
    "initial": "badge",
    "actors": {"reception": {"type": "human", "group": "reception"}, "platform": {"type": "system"}},
    "states": {
        "badge": {"display": "Badge to hand over", "kind": "wait"},
        "handed": {"display": "Badge handed over", "kind": "wait"},
        "to_review": {"display": "To review", "kind": "wait"},
        "active": {"display": "Active badge", "terminal": True},
    },
    "transitions": [
        {
            "id": "t-badge",
            "from": "badge",
            "to": "handed",
            "by": "reception",
            "task": {"title": "Hand over the badge", "form": FORM, "attest": ATTESTED},
        },
        {
            "id": "t-activate",
            "from": "handed",
            "to": "active",
            "by": "platform",
            "action": {
                "kind": "badge.activate",
                "title": "Activate the badge {{ fields.badge_uid }}",
                "params": {"uid": "{{ fields.badge_uid }}", "bearer": "{{ fields.upn }}"},
                "effects": [
                    {
                        "effect": "connector.call",
                        "with": {
                            "connector": "badges",
                            "operation": "activate_badge",
                            "arguments": {"uid": "{{ params.uid }}", "holder": "{{ params.bearer }}"},
                        },
                    }
                ],
            },
            "on_fail": {"to": "handed", "max_attempts": 1, "escalate_to": "to_review"},
        },
    ],
}


@pytest.fixture
def readers(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    import choregos_adapters
    from choregos_adapters.fakes.hr import FakeBadges

    fake = FakeBadges(expected_key="cle-des-readers")
    monkeypatch.setitem(choregos_adapters.FAUX_METIER, "access_control", fake)
    monkeypatch.setenv("CONNECTOR_TEST_READERS_KEY", "cle-des-readers")
    yield fake


async def _set_up(setup: Fixture) -> None:
    """The organisation's badge readers (type `demo`, key by reference), `activate_badge`
    open (`allowed`); the workflow; Lea's ticket, at the task."""
    from choregos_adapters import spec_of
    from choregos_api.db.models import ConnectorOperation, OrgConnector, Project, WorkflowDef, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_contracts import Workflow

    spec = spec_of("access_control", "demo")
    assert spec is not None
    activate = next(o for o in spec.operations if o.name == "activate_badge")
    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        readers = OrgConnector(
            org_id=project.org_id,
            name="badges",
            kind="access_control",
            type="demo",
            config={},
            secret_refs={"api_key": "env:CONNECTOR_TEST_READERS_KEY"},
        )
        session.add(readers)
        await session.flush()
        session.add(
            ConnectorOperation(
                org_id=project.org_id,
                connector_id=readers.id,
                name="activate_badge",
                access="write",
                policy="allowed",
                groups=[],
                input_schema=activate.input_schema,
            )
        )
        model = Workflow.model_validate(BADGE)
        row = WorkflowDef(
            project_id=setup.project_id,
            name="onboarding",
            version=1,
            source="platform",
            yaml="",
            json_doc=model.model_dump(mode="json", by_alias=True, exclude_none=True),
            checksum="sha256:onboarding-badge",
            is_active=True,
        )
        session.add(row)
        await session.flush()
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.workflow_def_id, item.state, item.fields = row.id, "badge", {"upn": "lea@acme.test"}


async def _wait_for(handle: Any, predicate: Any, what: str) -> dict[str, Any]:
    for _ in range(600):
        status = await handle.query("status")
        if predicate(status):
            return dict(status)
        await asyncio.sleep(0.05)
    raise AssertionError(f"{what}: never true (status: {await handle.query('status')})")


async def test_the_uid_entered_in_the_task_reaches_the_activate_badge_parameters(
    setup: Fixture, temporal_env: Any, worker_factory: Any, readers: Any
) -> None:
    from choregos_api.db.models import Action, HumanRequest, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from sqlalchemy import select
    from temporalio.api.enums.v1 import EventType
    from temporalio.worker import Replayer

    await _set_up(setup)
    async with worker_factory():
        handle = await temporal_env.client.start_workflow(
            "WorkflowInterpreter",
            {
                "project_id": setup.project_id,
                "project_slug": setup.project_slug,
                "work_item_id": setup.work_item_id,
                "tracker_key": setup.tracker_key,
            },
            id=f"wi-{setup.project_slug}-badge",
            task_queue="test",
        )
        status = await _wait_for(handle, lambda s: s.get("pending_request"), "the task is requested")
        async with session_scope() as session:
            request = await session.get(HumanRequest, status["pending_request"])
            assert request is not None
            assert (request.kind, request.payload["form"], request.payload["attest"]) == (
                "task",
                FORM,
                ATTESTED,
            )
            # What the API does (`complete`): the values in the fields, then the decision to the ticket.
            item = await session.get(WorkItem, setup.work_item_id)
            assert item is not None
            item.fields = {**(item.fields or {}), "badge_uid": "04A1B2C3"}
        await handle.signal(
            "human_decision",
            {
                "request_id": status["pending_request"],
                "kind": "task",
                "approved": True,
                "values": {"badge_uid": "04A1B2C3"},
                "attestation": ATTESTED,
                "decided_by": "reception@acme.test",
            },
        )
        result = await handle.result()
        history = await handle.fetch_history()
    assert result["state"] == "active"
    assert readers.badges == {"04A1B2C3": {"uid": "04A1B2C3", "holder": "lea@acme.test", "active": True}}
    signals = [
        e.workflow_execution_signaled_event_attributes.signal_name
        for e in history.events
        if e.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_SIGNALED
    ]
    assert "action_settled" in signals, (
        "the action notifies the interpreter that proposed it, under ITS identifier"
    )
    async with session_scope() as session:
        (action,) = (await session.execute(select(Action))).scalars()
        assert action.params == {"uid": "04A1B2C3", "bearer": "lea@acme.test"}
        assert action.title == "Activate the badge 04A1B2C3" and action.status == "succeeded"
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        target = HISTORIES / "wi-human-task-badge-S20-06.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_a_date_entered_in_the_task_arms_the_action_that_depends_on_it(
    setup: Fixture, temporal_env: Any, worker_factory: Any, readers: Any
) -> None:
    """The task fills in the date the next action waits for (`not_before`): its values enter the
    fields the interpreter holds, not only the database."""
    from choregos_api.db.models import WorkflowDef, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_contracts import Workflow

    await _set_up(setup)
    document = json.loads(json.dumps(BADGE))
    document["metadata"]["inputs"]["properties"]["handed_on"] = {"type": "string", "format": "date-time"}
    document["transitions"][0]["task"]["form"]["properties"]["handed_on"] = {"type": "string"}
    document["transitions"][1]["action"]["not_before"] = "fields.handed_on + 1h"
    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        row = await session.get(WorkflowDef, item.workflow_def_id)
        assert row is not None
        row.json_doc = Workflow.model_validate(document).model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
    start = await temporal_env.get_current_time()
    values = {"badge_uid": "04A1B2C3", "handed_on": (start - timedelta(hours=2)).isoformat()}
    async with worker_factory():
        handle = await temporal_env.client.start_workflow(
            "WorkflowInterpreter",
            {
                "project_id": setup.project_id,
                "project_slug": setup.project_slug,
                "work_item_id": setup.work_item_id,
                "tracker_key": setup.tracker_key,
            },
            id=f"wi-{setup.project_slug}-badge-date",
            task_queue="test",
        )
        status = await _wait_for(handle, lambda s: s.get("pending_request"), "the task is requested")
        async with session_scope() as session:
            item = await session.get(WorkItem, setup.work_item_id)
            assert item is not None
            item.fields = {**(item.fields or {}), **values}
        await handle.signal(
            "human_decision",
            {"request_id": status["pending_request"], "kind": "task", "approved": True, "values": values,
             "attestation": ATTESTED, "decided_by": "reception@acme.test"},
        )  # fmt: skip
        # Without advancing the clock: the date entered is an hour in the past, the action leaves at once.
        await _wait_for(handle, lambda s: s["state"] == "active", "the badge is active")
    assert readers.badges["04A1B2C3"]["active"] is True
