"""The `ActionWorkflow` (ADR 0035, S20-01): an approved action runs effect by effect, each one
recorded under its key; a transient failure is retried without redoing what is done; a refusal
stops the rest and what was done is compensated, in reverse.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Iterator
from typing import Any

import pytest

from .conftest import Fixture, recording_history

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"


class Journal:
    """What the fake effects really did, in order — the truth of the outside world."""

    def __init__(self) -> None:
        self.done: list[tuple[str, dict[str, Any]]] = []
        self.outages: dict[str, int] = {}


@pytest.fixture
def journal() -> Iterator[Journal]:
    from choregos_api import effects
    from choregos_api.effects import EffectRefused

    seen = Journal()

    async def create(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        seen.done.append(("create", params))
        return {"id": f"account-{params['upn']}"}

    async def add(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        remaining = seen.outages.get("add", 0)
        if remaining:
            seen.outages["add"] = remaining - 1
            raise RuntimeError("503 from the provider, transient")
        seen.done.append(("add", params))
        return {"added": params["group"]}

    async def refuse(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        seen.done.append(("refuse", params))
        raise EffectRefused("the account is outside the administrative unit")

    async def undo(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        seen.done.append(("undo", params))
        return {}

    for name, function in (
        ("test.create", create),
        ("test.add", add),
        ("test.refuse", refuse),
        ("test.undo", undo),
    ):
        effects.declare_effect(name, function)
    yield seen
    effects.reset()


CREATE = {
    "effect": "test.create",
    "with": {"upn": "{{ params.upn }}"},
    "compensate": {"effect": "test.undo", "with": {"deactivate": "{{ result.id }}"}},
}
ADD = {
    "effect": "test.add",
    "with": {"account": "{{ effects[0].id }}", "group": "devs"},
    "compensate": {"effect": "test.undo", "with": {"remove": "devs"}},
}
WITHOUT_COMPENSATION = {"effect": "test.add", "with": {"account": "{{ effects[0].id }}", "group": "vpn"}}
REFUSE = {"effect": "test.refuse", "with": {}}  # fmt: skip


async def _action(setup: Fixture, effect_list: list[dict[str, Any]], status: str = "approved") -> str:
    from choregos_api.db.models import Action, Project
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        action = Action(
            org_id=project.org_id,
            project_id=project.id,
            origin="transition",
            kind="arrival.accounts",
            title="The accounts of Lea",
            params={"upn": "lea@acme.test"},
            effects=effect_list,
            proposed_by={"kind": "system", "id": "system"},
            approval={},
            decisions=[],
            status=status,
        )
        session.add(action)
        await session.flush()
        return str(action.id)


async def _run(temporal_env: Any, worker_factory: Any, action_id: str) -> tuple[dict[str, Any], Any]:
    async with worker_factory():
        handle = await temporal_env.client.start_workflow(
            "ActionWorkflow", {"action_id": action_id}, id=f"action-{action_id}", task_queue="test"
        )
        return await handle.result(), await handle.fetch_history()


async def _state(action_id: str) -> tuple[str, list[tuple[str, str, int]]]:
    from choregos_api.db.models import Action, ActionEffect
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        action = await session.get(Action, action_id)
        assert action is not None
        rows = (
            await session.execute(
                select(ActionEffect)
                .where(ActionEffect.action_id == action_id)
                .order_by(ActionEffect.position)
            )
        ).scalars()
        return action.status, [(e.key.split(":")[1], e.status, e.attempts) for e in rows]


async def test_a_transient_failure_does_not_redo_the_effect_already_done(
    setup: Fixture, temporal_env: Any, worker_factory: Any, journal: Journal
) -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    journal.outages["add"] = 1
    action_id = await _action(setup, [CREATE, ADD])
    result, history = await _run(temporal_env, worker_factory, action_id)
    assert result == {"status": "succeeded", "effects": [0, 1]}
    assert journal.done == [
        ("create", {"upn": "lea@acme.test"}),
        ("add", {"account": "account-lea@acme.test", "group": "devs"}),
    ], "the account was created only once; the second effect cites the first"
    status, effect_rows = await _state(action_id)
    assert status == "succeeded"
    assert effect_rows == [("0", "done", 1), ("1", "done", 2)], (
        "two attempts for the second, one for the first"
    )

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        target = HISTORIES / "action-S20-01.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


@pytest.mark.parametrize("status", ["pending_approval", "rejected", "succeeded", "failed"])
async def test_a_workflow_started_without_a_committed_approval_runs_nothing(
    setup: Fixture, temporal_env: Any, worker_factory: Any, journal: Journal, status: str
) -> None:
    """Fail closed (ADR 0085): a decision whose transaction failed, a hand-made start, a finished
    action started again — `load_action` refuses before the first effect, whatever the effect is,
    and leaves the row as it found it."""
    from temporalio.client import WorkflowFailureError

    action_id = await _action(setup, [CREATE, ADD], status=status)
    with pytest.raises(WorkflowFailureError) as failure:
        await _run(temporal_env, worker_factory, action_id)
    assert "not approved" in str(failure.value.cause.cause)
    assert journal.done == [], "no effect, no compensation"
    assert await _state(action_id) == (status, []), "the row is not closed, no effect is logged"


async def test_a_refusal_compensates_what_was_done_in_reverse(
    setup: Fixture, temporal_env: Any, worker_factory: Any, journal: Journal
) -> None:
    action_id = await _action(setup, [CREATE, ADD, WITHOUT_COMPENSATION, REFUSE])
    result, _ = await _run(temporal_env, worker_factory, action_id)
    assert result["status"] == "failed" and "outside the administrative unit" in result["error"]
    assert [name for name, _ in journal.done] == [
        "create",
        "add",
        "add",
        "refuse",
        "undo",
        "undo",
    ]
    assert journal.done[-2:] == [
        ("undo", {"remove": "devs"}),
        ("undo", {"deactivate": "account-lea@acme.test"}),
    ]
    assert [c["undone"] for c in result["compensations"]] == [False, True, True], (
        "in reverse; what it could not undo, it says so"
    )
    status, effect_rows = await _state(action_id)
    assert status == "failed"
    assert [e[1] for e in effect_rows] == ["compensated", "compensated", "compensation_failed", "started"]


async def test_a_done_key_is_not_redone_a_started_key_is_resumed(setup: Fixture, journal: Journal) -> None:
    """The worker is killed: before the confirmation, the effect is resumed (it is idempotent);
    after, the recorded answer comes back and nothing is sent again to the third-party system."""
    from choregos_api.db.models import ActionEffect
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.actions import execute_effect

    action_id = await _action(setup, [CREATE])
    async with session_scope() as session:
        from choregos_api.db.models import Action

        action = await session.get(Action, action_id)
        assert action is not None
        session.add(
            ActionEffect(
                org_id=action.org_id,
                action_id=action_id,
                position=0,
                key=f"{action_id}:0",
                effect="test.create",
                params={},
                status="started",
                attempts=1,
            )
        )
    first = await execute_effect({"action_id": action_id, "position": 0})
    second = await execute_effect({"action_id": action_id, "position": 0})
    assert first["replayed"] is False and second["replayed"] is True
    assert second["result"] == {"id": "account-lea@acme.test"}
    assert journal.done == [("create", {"upn": "lea@acme.test"})], "a single creation at the third party"
    _, effect_rows = await _state(action_id)
    assert effect_rows == [("0", "done", 2)]


async def test_an_approved_action_reaches_the_server_with_the_connector_key(
    setup: Fixture, temporal_env: Any, worker_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end on the orchestrator side (S20-02): the tool under approval became an action, it
    was approved; the `ActionWorkflow` runs it, and it is IT that reaches the server — with the
    connector key, resolved by the platform."""
    import choregos_adapters
    from choregos_adapters.fakes.mcp import FakeMcpServer
    from choregos_api.db.models import ConnectorOperation, OrgConnector, Project
    from choregos_api.db.session import session_scope

    server = FakeMcpServer(
        tools=[{"name": "order_workstation", "inputSchema": {"type": "object"}}], token="provider-key"
    )
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP", server)
    monkeypatch.setenv("CONNECTOR_TEST_PROVIDER_KEY", "provider-key")
    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        instance = OrgConnector(
            org_id=project.org_id,
            name="provider",
            kind="mcp",
            type="mcp",
            config={"url": "https://provider.test/mcp"},
            secret_refs={"token": "env:CONNECTOR_TEST_PROVIDER_KEY"},
        )
        session.add(instance)
        await session.flush()
        session.add(
            ConnectorOperation(
                org_id=project.org_id,
                connector_id=instance.id,
                name="order_workstation",
                access="write",
                policy="approval",
                groups=[],
            )
        )
    effect = {
        "effect": "connector.call",
        "with": {
            "connector": "provider",
            "operation": "order_workstation",
            "arguments": {"model": "laptop-14"},
        },
    }
    action_id = await _action(setup, [effect])
    result, _ = await _run(temporal_env, worker_factory, action_id)
    assert result["status"] == "succeeded", result
    assert server.calls == [("order_workstation", {"model": "laptop-14"})]
    assert {headers["authorization"] for _, headers, _ in server.received} == {"Bearer provider-key"}


async def test_an_action_started_before_its_row_is_visible_waits_until_it_is(
    setup: Fixture, temporal_env: Any, worker_factory: Any, journal: Journal
) -> None:
    """The request that decides starts `action-<id>` BEFORE committing its transaction (S20-08): the
    row may not be visible yet when the workflow reads it. It retries, instead of dying."""
    import asyncio

    from choregos_api.db.models import Action, Project
    from choregos_api.db.session import session_scope

    action_id = "00000000-0000-7000-8000-00000000a7a1"
    async with worker_factory():
        handle = await temporal_env.client.start_workflow(
            "ActionWorkflow", {"action_id": action_id}, id=f"action-{action_id}", task_queue="test"
        )
        await asyncio.sleep(0.3)
        async with session_scope() as session:
            project = await session.get(Project, setup.project_id)
            assert project is not None
            session.add(
                Action(
                    id=action_id,
                    org_id=project.org_id,
                    project_id=project.id,
                    origin="transition",
                    kind="arrival.accounts",
                    title="The accounts of Lea",
                    params={"upn": "lea@acme.test"},
                    effects=[CREATE],
                    proposed_by={"kind": "system", "id": "system"},
                    approval={},
                    decisions=[],
                    status="approved",
                )
            )
        result = await handle.result()
    assert result["status"] == "succeeded"
    assert journal.done == [("create", {"upn": "lea@acme.test"})]
