"""Fan-out and join (S38-12, ADR 0057), on the test Temporal server.

A system transition with `spawn:` opens one child work item per element of a list, starts each
child's interpreter, and passes only once every child is settled. Each child signals its parent as
it closes; the parent also re-reads its children in case a signal is lost. Opening is idempotent.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
from typing import Any

import pytest

from .conftest import Fixture

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"
PARENT_ID = "wi-billing-api-varga_billing-api-123"
APPROVE = {"approved": True, "decided_by": "ana", "kind": "approval"}
REJECT = {"approved": False, "decided_by": "ana", "kind": "approval", "reason": "too volatile"}


def _research(min_succeeded: int = 1, max_children: int = 20) -> dict[str, Any]:
    return {
        "apiVersion": "choregos/v1",
        "kind": "Workflow",
        "metadata": {"name": "research", "version": 1},
        "initial": "screened",
        "actors": {"platform": {"type": "system"}},
        "states": {
            "screened": {"display": "Screened", "kind": "wait"},
            "decided": {"display": "Decided", "terminal": True},
            "nothing_adopted": {"display": "Nothing adopted", "terminal": True},
        },
        "transitions": [
            {
                "id": "t-review-each",
                "from": "screened",
                "to": "decided",
                "by": "platform",
                "spawn": {
                    "workflow": "review",
                    "for_each": "fields.symbols",
                    "title": "Review {{ item.symbol }} ({{ parent.key }})",
                    "fields": {"symbol": "{{ item.symbol }}", "dsr": "{{ item.dsr }}"},
                    "max_children": max_children,
                    "succeeded": ["adopted"],
                    "min_succeeded": min_succeeded,
                },
                "on_fail": {"to": "screened", "max_attempts": 1, "escalate_to": "nothing_adopted"},
            }
        ],
    }


def _review() -> dict[str, Any]:
    return {
        "apiVersion": "choregos/v1",
        "kind": "Workflow",
        "metadata": {
            "name": "review",
            "version": 1,
            "inputs": {
                "type": "object",
                "required": ["symbol"],
                "properties": {"symbol": {"type": "string"}, "dsr": {"type": "number"}},
            },
        },
        "initial": "proposed",
        "actors": {"owner": {"type": "human", "group": "owner"}},
        "states": {
            "proposed": {"display": "Proposed", "kind": "wait"},
            "adopted": {"display": "Adopted", "terminal": True},
            "rejected": {"display": "Rejected", "terminal": True},
        },
        "transitions": [
            {"id": "t-adopt", "from": "proposed", "to": "adopted", "by": "owner", "on_reject": "rejected"}
        ],
    }


SYMBOLS = [{"symbol": "AAA", "dsr": 0.97}, {"symbol": "BBB", "dsr": 0.91}]


async def _install(setup: Fixture, research: dict[str, Any], fields: dict[str, Any]) -> None:
    from choregos_adapters.tracker.interne import InternalTracker
    from choregos_api.db.models import WorkflowDef, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_contracts import Workflow

    setup.adapters.tracker = InternalTracker()
    async with session_scope() as session:
        rows = {}
        for document in (research, _review()):
            model = Workflow.model_validate(document)
            row = WorkflowDef(
                project_id=setup.project_id,
                name=model.metadata.name,
                version=1,
                source="platform",
                yaml="",
                json_doc=model.model_dump(mode="json", by_alias=True, exclude_none=True),
                checksum=f"sha256:{model.metadata.name}",
                is_active=True,
            )
            session.add(row)
            rows[model.metadata.name] = row
        await session.flush()
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.workflow_def_id, item.state, item.fields = rows["research"].id, "screened", fields


async def _start(env: Any, setup: Fixture) -> Any:
    return await env.client.start_workflow(
        "WorkflowInterpreter",
        {
            "project_id": setup.project_id,
            "project_slug": setup.project_slug,
            "work_item_id": setup.work_item_id,
            "tracker_key": setup.tracker_key,
        },
        id=PARENT_ID,
        task_queue="test",
    )


async def _until(what: str, probe: Any) -> Any:
    for _ in range(600):
        value = await probe()
        if value:
            return value
        await asyncio.sleep(0.05)
    raise AssertionError(f"{what}: never true")


async def _items(setup: Fixture) -> dict[str, Any]:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = await session.execute(select(WorkItem).where(WorkItem.project_id == setup.project_id))
        return {row.tracker_key: row for row in rows.scalars()}


async def _children_open(setup: Fixture, count: int) -> dict[str, Any]:
    async def probe() -> dict[str, Any] | None:
        items = await _items(setup)
        children = {k: v for k, v in items.items() if k != setup.tracker_key}
        return (
            children if len(children) == count and all(v.temporal_wf_id for v in children.values()) else None
        )

    children: dict[str, Any] = await _until(f"{count} children opened", probe)
    return children


async def _waiting(env: Any, workflow_id: str) -> None:
    handle = env.client.get_workflow_handle(workflow_id)

    async def probe() -> bool:
        return bool((await handle.query("status")).get("state") == "proposed")

    await _until(f"{workflow_id} waits for its owner", probe)


async def test_the_parent_waits_for_every_child_then_reads_what_they_concluded(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.api.enums.v1 import EventType
    from temporalio.worker import Replayer

    await _install(setup, _research(), {"symbols": SYMBOLS})
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            parent = await _start(temporal_env, setup)
            children = await _children_open(setup, 2)
            assert sorted(children) == ["varga/billing-api#123.1", "varga/billing-api#123.2"]
            first, second = children["varga/billing-api#123.1"], children["varga/billing-api#123.2"]
            assert first.title == "Review AAA (varga/billing-api#123)"
            assert first.fields == {"symbol": "AAA", "dsr": 0.97}, "one expression keeps its type"
            assert first.documents["parent"]["workflow_id"] == PARENT_ID
            assert first.documents["labels"] == ["parent:varga/billing-api#123"]
            for child in (first, second):
                await _waiting(temporal_env, child.temporal_wf_id)

            await temporal_env.client.get_workflow_handle(first.temporal_wf_id).signal(
                "human_decision", APPROVE
            )
            await temporal_env.client.get_workflow_handle(first.temporal_wf_id).result()

            async def one_left() -> bool:
                status = await parent.query("status")
                return bool(status["waiting_until"] == "1 of 2 child work items not settled")

            await _until("the parent re-reads after the first child", one_left)
            status = await parent.query("status")
            assert status["state"] == "screened", f"one child is still open, the parent waits: {status}"

            await temporal_env.client.get_workflow_handle(second.temporal_wf_id).signal(
                "human_decision", REJECT
            )
            result = await parent.result()
            history = await parent.fetch_history()

    assert result["state"] == "decided", result
    joined = (await _items(setup))[setup.tracker_key].documents["children"]
    assert joined["total"] == 2 and joined["succeeded"] == 1, joined
    assert joined["states"] == {"adopted": 1, "rejected": 1}
    assert [c["key"] for c in joined["items"]] == ["varga/billing-api#123.1", "varga/billing-api#123.2"]
    signals = [
        e.workflow_execution_signaled_event_attributes.signal_name
        for e in history.events
        if e.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_SIGNALED
    ]
    assert signals.count("child_settled") == 2, "each child tells its parent: no wait for the re-read"
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if os.environ.get("CHOREGOS_ARCHIVER_HISTORIQUE"):
        target = HISTORIES / "wi-fan-out-S38-12.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_too_few_children_succeeding_fails_the_transition(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    await _install(setup, _research(min_succeeded=1), {"symbols": SYMBOLS[:1]})
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            parent = await _start(temporal_env, setup)
            (child,) = (await _children_open(setup, 1)).values()
            await _waiting(temporal_env, child.temporal_wf_id)
            await temporal_env.client.get_workflow_handle(child.temporal_wf_id).signal(
                "human_decision", REJECT
            )
            result = await parent.result()
    assert result["state"] == "nothing_adopted", "no child adopted, one required: on_fail"
    joined = (await _items(setup))[setup.tracker_key].documents["children"]
    assert joined["succeeded"] == 0 and joined["states"] == {"rejected": 1}


async def test_a_list_too_long_is_refused_and_opens_nothing(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    await _install(setup, _research(max_children=1), {"symbols": SYMBOLS})
    async with worker_factory():
        result = await (await _start(temporal_env, setup)).result()
    assert result["state"] == "nothing_adopted"
    assert list(await _items(setup)) == [setup.tracker_key], "a refused fan-out opens no child"


async def test_a_lost_signal_is_caught_up_by_the_re_read(
    setup: Fixture, temporal_env: Any, worker_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without `child_settled`, the parent still learns at its next re-read (time skipped)."""
    from choregos_orchestrator.activities import children as children_activities

    async def lost(_settled: dict[str, str]) -> None:
        return None

    monkeypatch.setattr(children_activities, "notify_parent", lost)
    await _install(setup, _research(), {"symbols": SYMBOLS[:1]})
    async with worker_factory():
        parent = await _start(temporal_env, setup)
        (child,) = (await _children_open(setup, 1)).values()
        await _waiting(temporal_env, child.temporal_wf_id)
        await temporal_env.client.get_workflow_handle(child.temporal_wf_id).signal("human_decision", APPROVE)
        result = await parent.result()
        history = await parent.fetch_history()
    assert result["state"] == "decided"
    assert "child_settled" not in history.to_json(), "no signal reached the parent: the re-read did"


async def test_opening_twice_opens_nothing_new(setup: Fixture) -> None:
    """A retried activity, a parent replayed after a crash: the children are found, not opened again."""
    from choregos_orchestrator.activities.children import spawn_children
    from temporalio.testing import ActivityEnvironment

    await _install(setup, _research(), {"symbols": SYMBOLS})
    started: list[str] = []

    class Client:
        async def start_workflow(self, _name: str, _input: Any, *, id: str, task_queue: str) -> None:
            started.append(id)

    env = ActivityEnvironment(client=Client())  # type: ignore[arg-type]
    payload = {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "workflow_id": PARENT_ID,
        "spawn": _research()["transitions"][0]["spawn"],
    }
    first = await env.run(spawn_children, payload)
    again = await env.run(spawn_children, payload)
    assert first == again == {"children": ["varga/billing-api#123.1", "varga/billing-api#123.2"]}
    assert len(await _items(setup)) == 3, "two children, not four"
    assert started == [f"{PARENT_ID}.1", f"{PARENT_ID}.2"] * 2, "the same identifiers: Temporal refuses twice"


def test_the_list_is_read_from_the_fields_or_from_an_output_kept_as_json() -> None:
    from choregos_orchestrator.activities.children import SpawnRefusedError, items_of

    assert items_of("fields.symbols", {"symbols": ["A"]}, {}) == ["A"]
    metrics = {"metrics": json.dumps({"relevant": [{"symbol": "A"}]})}
    assert items_of("outputs.metrics.relevant", {}, metrics) == [{"symbol": "A"}]
    with pytest.raises(SpawnRefusedError, match="does not exist"):
        items_of("outputs.metrics.relevant", {}, {})
    with pytest.raises(SpawnRefusedError, match="not a list"):
        items_of("fields.symbols", {"symbols": "A"}, {})


def test_the_rendering_keeps_a_type_and_refuses_what_it_cannot_render() -> None:
    from choregos_orchestrator.activities.children import SpawnRefusedError, render

    context = {"item": {"symbol": "A", "dsr": 0.9}, "rank": 2}
    assert render({"dsr": "{{ item.dsr }}", "t": "#{{ rank }} {{ item.symbol }}"}, context) == {
        "dsr": 0.9,
        "t": "#2 A",
    }
    with pytest.raises(SpawnRefusedError):
        render("{{ absent.key }}", context)
    with pytest.raises(SpawnRefusedError):  # the sandbox: no reaching the interpreter through an object
        render("{{ item.__class__.__mro__ }}", context)


async def test_the_local_mode_stops_at_a_fan_out_instead_of_passing_it(setup: Fixture) -> None:
    from choregos_orchestrator.local import run_local

    await _install(setup, _research(), {"symbols": SYMBOLS})
    trace = await run_local(setup.project_id, setup.work_item_id)
    assert trace.final_state == "screened", "no child opened: the parent did not move"
    assert "does not run a fan-out" in (trace.stopped_reason or "")
    assert list(await _items(setup)) == [setup.tracker_key]
