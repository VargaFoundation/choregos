"""An effect may end in doubt, and so may its action (S65-14, ADR 0149 §5).

An effect that was sent and that nobody knows was executed is neither a refusal nor a failure to
retry: the `ActionWorkflow` closes the action `in_doubt`, a terminal status, attempts the effect no
more and compensates nothing of what the action had done, since both would act on a state nobody
knows. The one exception is an effect that says the same key may be delivered again: its retries
are the workflow's, and a doubt that outlives them is still a doubt, never a failure.

The effects here are the suite's own (`test.*`): what the workflow and its activities do with a
doubt, whatever raised it. The write through an outpost that raises one for real is proven in
`apps/api/tests/test_write_via_outpost.py`.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Iterator
from typing import Any

import pytest
from temporalio.exceptions import ApplicationError

from .conftest import Fixture, recording_history

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"

SENT = {
    "outpost": "factory-floor",
    "endpoint": "tracker",
    "operation": "create_issue",
    "call_id": "c-1",
    "instance_id": "i-1",
    "claimed_at": "2026-11-12T10:00:00+00:00",
    "arguments_sha256": "0" * 64,
}


class World:
    """What the effects really did, in order, and what each attempt was told of the earlier ones."""

    def __init__(self) -> None:
        self.done: list[str] = []
        #: The context of every attempt of `test.again`: its key, and the doubts it was handed.
        self.attempts: list[tuple[str | None, tuple[dict[str, Any], ...]]] = []
        #: How many deliveries of `test.again` end in doubt before one is answered.
        self.doubts_before_an_answer = 1
        #: What an attempt of `test.again` meets once a delivery is in doubt, instead of an answer.
        self.then: BaseException | None = None
        self.settled: list[str] = []


@pytest.fixture
def world() -> Iterator[World]:
    from choregos_api import effects
    from choregos_api.effects import EffectInDoubt

    seen = World()

    async def create(_ctx: Any, _params: dict[str, Any]) -> dict[str, Any]:
        seen.done.append("create")
        return {"id": "account-1"}

    async def undo(_ctx: Any, _params: dict[str, Any]) -> dict[str, Any]:
        seen.done.append("undo")
        return {}

    async def doubt(ctx: Any, _params: dict[str, Any]) -> dict[str, Any]:
        seen.done.append("doubt")

        async def settle(_session: Any) -> None:
            seen.settled.append(str(ctx.key))

        ctx.once_durable.append(settle)
        raise EffectInDoubt("the write was claimed and never answered", sent=dict(SENT))

    async def again(ctx: Any, _params: dict[str, Any]) -> dict[str, Any]:
        seen.attempts.append((ctx.key, ctx.doubts))
        seen.done.append("again")
        if len(ctx.doubts) < seen.doubts_before_an_answer:
            sent = SENT | {"call_id": f"c-{len(ctx.doubts) + 1}"}
            raise EffectInDoubt("claimed and never answered", sent=sent, again=True)
        if seen.then is not None:
            raise seen.then
        return {"delivered": len(ctx.doubts) + 1}

    for name, function in (
        ("test.create", create),
        ("test.undo", undo),
        ("test.doubt", doubt),
        ("test.again", again),
    ):
        effects.declare_effect(name, function)
    yield seen
    effects.reset()


CREATE = {"effect": "test.create", "with": {}, "compensate": {"effect": "test.undo", "with": {}}}
DOUBT = {"effect": "test.doubt", "with": {}, "compensate": {"effect": "test.undo", "with": {}}}
AGAIN = {"effect": "test.again", "with": {}}


async def _action(setup: Fixture, effects: list[dict[str, Any]]) -> str:
    from choregos_api.db.models import Action, Project
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        action = Action(
            org_id=project.org_id,
            project_id=project.id,
            origin="tool",
            kind="tracker.create_issue",
            title="An issue at the outpost",
            params={},
            effects=effects,
            proposed_by={"kind": "system", "id": "system"},
            approval={},
            decisions=[],
            status="approved",
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


async def _state(action_id: str) -> tuple[Any, list[Any]]:
    from choregos_api.db.models import Action, ActionEffect
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        action = await session.get(Action, action_id)
        assert action is not None
        rows = await session.execute(
            select(ActionEffect).where(ActionEffect.action_id == action_id).order_by(ActionEffect.position)
        )
        return action, list(rows.scalars())


async def _events(action_id: str) -> list[tuple[str, dict[str, Any]]]:
    from choregos_api.db.models import Event
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = await session.execute(select(Event).where(Event.subject == action_id).order_by(Event.ts))
        return [(event.type, dict(event.payload or {})) for event in rows.scalars()]


async def test_an_effect_in_doubt_closes_the_action_in_doubt_and_compensates_nothing(
    setup: Fixture, temporal_env: Any, worker_factory: Any, world: World
) -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    action_id = await _action(setup, [CREATE, DOUBT, CREATE])
    result, history = await _run(temporal_env, worker_factory, action_id)
    assert result["status"] == "in_doubt" and result["effects"] == [0], result
    assert "never answered" in result["error"]
    assert world.done == ["create", "doubt"], (
        "sent once and never again; what was done before it is not undone; what came after never ran"
    )
    action, effects = await _state(action_id)
    assert action.status == "in_doubt" and action.finished_at is not None
    assert "never answered" in (action.error or "")
    assert [(row.status, row.attempts) for row in effects] == [("done", 1), ("in_doubt", 1)]
    # What a person is shown: on the effect, and on the action with the position of the effect.
    assert effects[1].result == {"in_doubt": [SENT], "again": False}
    assert action.result["in_doubt"] == {"position": 1, "sent": [SENT]}
    assert action.result["compensations"] == []
    # What the effect kept elsewhere until its outcome was durable goes with the record of the doubt.
    assert world.settled == [f"{action_id}:1"]
    # No event type of its own: the terminal event that says "it did not succeed" carries the status.
    closing = [
        (kind, data) for kind, data in await _events(action_id) if kind.endswith((".failed", ".succeeded"))
    ]
    assert [(kind, data["status"]) for kind, data in closing] == [("choregos.action.failed", "in_doubt")]

    # One attempt of the effect in doubt, and final: the workflow's policy did not retry it.
    started = [
        event.activity_task_started_event_attributes.attempt
        for event in history.events
        if event.HasField("activity_task_started_event_attributes")
    ]
    assert started == [1, 1, 1, 1], "load, two effects and the close, each attempted once"

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        target = HISTORIES / "action-in-doubt-S65-14.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_a_final_doubt_is_not_attempted_again(setup: Fixture, world: World) -> None:
    """Temporal may deliver a task twice (a worker that recorded the doubt and died before it said
    so): the effect is not played a second time, and the answer is the same."""
    from choregos_orchestrator.activities.actions import execute_effect

    action_id = await _action(setup, [DOUBT])
    for _ in range(2):
        with pytest.raises(ApplicationError) as doubt:
            await execute_effect({"action_id": action_id, "position": 0})
        assert (doubt.value.type, doubt.value.non_retryable) == ("EffectInDoubt", True)
        assert "never answered" in str(doubt.value)
    assert world.done == ["doubt"], "played once"
    _action_row, (row,) = await _state(action_id)
    assert (row.status, row.attempts, row.result) == ("in_doubt", 1, {"in_doubt": [SENT], "again": False})


async def test_an_effect_that_may_be_delivered_again_is_retried_by_the_workflow(
    setup: Fixture, temporal_env: Any, worker_factory: Any, world: World
) -> None:
    """The one exception (ADR 0149 §5): the doubt is retryable, the workflow's own policy retries
    it, and the attempt that follows is told what was already sent."""
    action_id = await _action(setup, [CREATE, AGAIN])
    result, _history = await _run(temporal_env, worker_factory, action_id)
    assert result == {"status": "succeeded", "effects": [0, 1]}
    key = f"{action_id}:1"
    assert world.attempts == [(key, ()), (key, (SENT | {"call_id": "c-1"},))]
    action, effects = await _state(action_id)
    assert action.status == "succeeded"
    assert [(row.status, row.attempts) for row in effects] == [("done", 1), ("done", 2)]
    assert effects[1].result == {"delivered": 2} and effects[1].error is None


async def test_a_doubt_that_outlives_its_retries_is_still_a_doubt(
    setup: Fixture, temporal_env: Any, worker_factory: Any, world: World
) -> None:
    """Every delivery ends in doubt until the workflow's policy gives up: the action is in doubt,
    not failed, and nothing is compensated."""
    world.doubts_before_an_answer = 99
    action_id = await _action(setup, [CREATE, AGAIN])
    result, _history = await _run(temporal_env, worker_factory, action_id)
    assert result["status"] == "in_doubt", result
    assert world.done == ["create", *["again"] * 6], "the workflow's six attempts, and no compensation"
    action, effects = await _state(action_id)
    assert action.status == "in_doubt"
    assert [(row.status, row.attempts) for row in effects] == [("done", 1), ("in_doubt", 6)]
    assert [sent["call_id"] for sent in effects[1].result["in_doubt"]] == [f"c-{n}" for n in range(1, 7)]
    assert effects[1].result["again"] is True


@pytest.mark.parametrize("final", [True, False], ids=["a refusal", "a failure that is retried"])
async def test_what_follows_a_doubt_does_not_make_it_a_failure(
    setup: Fixture, temporal_env: Any, worker_factory: Any, world: World, final: bool
) -> None:
    """One delivery is in doubt; the attempt that follows is refused, or fails until the retries
    are spent. The effect did not fail: a delivery of it may have been executed, so the action is in
    doubt and what it had done is not compensated."""
    from choregos_api.effects import EffectRefused

    world.then = EffectRefused("the outpost is paused") if final else RuntimeError("503, transient")
    action_id = await _action(setup, [CREATE, AGAIN])
    result, _history = await _run(temporal_env, worker_factory, action_id)
    assert result["status"] == "in_doubt", result
    assert "undo" not in world.done
    assert world.done.count("again") == (2 if final else 6)
    action, effects = await _state(action_id)
    assert action.status == "in_doubt"
    assert [row.status for row in effects] == ["done", "in_doubt"]
    assert len(effects[1].result["in_doubt"]) == 1, "the one delivery that was sent and never answered"
    assert ("paused" if final else "503") in (effects[1].error or "")


async def test_the_activities_alone_compensate_nothing_and_close_in_doubt(
    setup: Fixture, world: World
) -> None:
    """The second line. A workflow that predates the status (an older worker during a rolling
    upgrade, a history replayed from before the patch) takes its failure branch: it asks each done
    effect to be compensated and the action to be closed `failed`. The activities answer for the
    action as it is: nothing is undone, and it is closed in doubt."""
    from choregos_orchestrator.activities.actions import close_action, compensate_effect, execute_effect

    action_id = await _action(setup, [CREATE, DOUBT])
    assert (await execute_effect({"action_id": action_id, "position": 0}))["result"] == {"id": "account-1"}
    with pytest.raises(ApplicationError):
        await execute_effect({"action_id": action_id, "position": 1})
    for position in (1, 0):
        undone = await compensate_effect({"action_id": action_id, "position": position})
        assert undone["undone"] is False and "in doubt" in undone["reason"], undone
    closed = await close_action({"action_id": action_id, "status": "failed", "error": "EffectInDoubt"})
    assert closed["status"] == "in_doubt"
    assert world.done == ["create", "doubt"], "no compensation ran"
    action, effects = await _state(action_id)
    assert action.status == "in_doubt"
    assert [row.status for row in effects] == ["done", "in_doubt"], "the done effect stays done"
    kinds = [(kind, data.get("status")) for kind, data in await _events(action_id)]
    assert ("choregos.action.failed", "in_doubt") in kinds
    assert not [kind for kind, _ in kinds if kind.endswith("effect_compensated")]


async def test_an_action_that_failed_or_succeeded_closes_as_it_always_did(
    setup: Fixture, world: World
) -> None:
    from choregos_orchestrator.activities.actions import close_action, execute_effect

    action_id = await _action(setup, [CREATE])
    await execute_effect({"action_id": action_id, "position": 0})
    assert (await close_action({"action_id": action_id, "status": "failed", "error": "refused"}))[
        "status"
    ] == "failed"
    kinds = [(kind, data.get("status")) for kind, data in await _events(action_id)]
    assert ("choregos.action.failed", "failed") in kinds
    assert (await close_action({"action_id": action_id, "status": "succeeded"}))["status"] == "succeeded"


def test_the_workflow_reads_the_type_the_activity_raises() -> None:
    from choregos_orchestrator.activities import actions as activities
    from choregos_orchestrator.workflows import action as workflow

    assert workflow.EFFECT_IN_DOUBT == activities.EFFECT_IN_DOUBT == "EffectInDoubt"
    # A doubt is retried only when its activity says so: the policy must not name the type.
    assert "EffectInDoubt" not in (workflow.EFFECT.non_retryable_error_types or [])


async def test_a_work_item_whose_action_is_in_doubt_waits_and_proposes_it_no_second_time(
    setup: Fixture, temporal_env: Any, worker_factory: Any, world: World
) -> None:
    """A transition's action ends in doubt. Read as a failure it would follow `on_fail`, whose retry
    proposes the action again under a new attempt: a second delivery of a write nobody knows was
    made. The transition waits instead, as it waits for an approval nobody gave yet: the work item
    stays where it is, and re-reading the action every six hours changes nothing."""
    from datetime import timedelta

    from choregos_api import effects

    from .test_dated_action import _actions, _arrival, _iso, _start, _wait_for, _work_item

    effects.declare_effect("test.doubt.allowed", effects.effect("test.doubt"), policy="allowed")
    document = _arrival()
    transition = document["transitions"][0]
    transition["action"]["effects"] = [{"effect": "test.doubt.allowed", "with": {}}]
    transition["on_fail"]["max_attempts"] = 3
    start = await temporal_env.get_current_time()
    await _work_item(setup, document, {"upn": "lea@acme.test", "arrival_date": _iso(start)})
    async with worker_factory():
        handle = await _start(temporal_env, setup)
        status = await _wait_for(handle, lambda s: s.get("action"), "the action is proposed")

        async def in_doubt() -> bool:
            (proposed,) = await _actions(setup)
            return bool(proposed.status == "in_doubt")

        for _ in range(200):
            if await in_doubt():
                break
            await temporal_env.sleep(timedelta(seconds=1))
        assert await in_doubt(), "the action ended in doubt"
        # Two re-reads of the action later, nothing has moved and nothing was proposed again.
        await temporal_env.sleep(timedelta(hours=13))
        waiting = await handle.query("status")
        assert (waiting["state"], waiting["action"]) == ("prepared", status["action"]), waiting
        (action,) = await _actions(setup)
        assert (action.id, action.status) == (status["action"], "in_doubt")
        await handle.signal("control", {"action": "stop"})
    assert world.done == ["doubt"], "sent once: the transition did not try again"


async def test_a_workflow_that_predates_the_status_undoes_nothing_and_its_history_still_replays(
    setup: Fixture, temporal_env: Any, worker_factory: Any, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rolling upgrade: an activity of this version answers a workflow task of the previous one,
    whose code has no branch for a doubt. It takes its failure branch: it asks for the compensation
    of what was done and for the action to be closed `failed`. The activities undo nothing and close
    the action in doubt. Replayed by today's code, that history holds no marker of the branch, so
    the failure branch is taken again: the same commands, no divergence."""
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from choregos_orchestrator.workflows import action as workflow
    from temporalio.worker import Replayer

    action_id = await _action(setup, [CREATE, DOUBT])
    with monkeypatch.context() as before:
        before.setattr(workflow, "ended_in_doubt", lambda _error: False)  # the code before the branch
        result, history = await _run(temporal_env, worker_factory, action_id)
    assert result["status"] == "failed", "what that code says of it"
    assert [(undone["undone"], "in doubt" in undone["reason"]) for undone in result["compensations"]] == [
        (False, True)
    ]
    assert world.done == ["create", "doubt"], "and nothing was undone"
    action, effects = await _state(action_id)
    assert action.status == "in_doubt", "what the activities recorded of it"
    assert [row.status for row in effects] == ["done", "in_doubt"]

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        target = HISTORIES / "action-in-doubt-before-the-patch-S65-14.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")
