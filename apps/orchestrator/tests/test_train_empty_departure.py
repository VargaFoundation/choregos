"""A departure that cannot happen does not make the train spin (#370, #515, S22-29).

Before the fix, `depart_now` raised `depart_requested`, which only `_depart` resets; but `_collect`
does not depart without a batch, and its wait returned at once (the condition was already true): the
train chained `check_window` activities without a pause until the relay (~2,000 events since S22-27).

Here, a REAL empty `ReleaseTrain` in front of a closed window (`wait_seconds` = 60) receives
`depart_now`, then time moves 10 minutes forward: the `check_window` activities are counted. Same
for a FROZEN train with a batch, after `depart_now` or a hotfix boarding.
"""

from __future__ import annotations

import json
import pathlib
from datetime import timedelta
from typing import Any

import pytest

from .conftest import recording_history

pytestmark = pytest.mark.asyncio

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"
#: 10 minutes at one look per minute: ~11 looks. The empty spin made hundreds.
CEILING = 15


def _doubles(looks: list[int]) -> dict[str, Any]:
    """A `release_train` train in front of a closed window, counting its `check_window` calls; every
    step of a departure succeeds."""
    from temporalio import activity

    @activity.defn(name="create_release")
    async def create_release(payload: dict[str, Any]) -> dict[str, Any]:
        return {"release_id": f"rel-{payload['batch_no']}"}

    def _succeeds(name: str) -> Any:
        @activity.defn(name=name)
        async def step(payload: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True}

        return step

    steps = ["promote", "run_smoke", "soak", "apply_terraform", "promote_canary_step", "verify_prod"]

    @activity.defn(name="load_train_config")
    async def load_train_config(payload: dict[str, Any]) -> dict[str, Any]:
        return {"mode": "release_train", "batch_min": 1, "batch_max": 8, "soak_minutes": 1}

    @activity.defn(name="check_window")
    async def check_window(payload: dict[str, Any]) -> dict[str, Any]:
        looks.append(1)
        return {"open": False, "due": False, "wait_seconds": 60, "next_departure": None}

    return {
        "load_train_config": load_train_config,
        "check_window": check_window,
        "create_release": create_release,
        **{name: _succeeds(name) for name in [*steps, "finish_release"]},
    }


async def test_a_departure_on_an_empty_train_does_not_make_it_spin(
    temporal_env: Any, worker_factory: Any
) -> None:
    looks: list[int] = []
    async with worker_factory(remplacements=_doubles(looks)):
        train = await temporal_env.client.start_workflow(
            "ReleaseTrain",
            {"project_slug": "empty", "env": "staging"},
            id="train-empty-staging",
            task_queue="test",
        )
        state = await train.query("status_query")
        assert state["status"] == "collecting" and state["batch_size"] == 0, state

        before = len(looks)
        await train.signal("depart_now", {"by": "test"})
        await temporal_env.sleep(timedelta(minutes=10))
        state = await train.query("status_query")
        after = len(looks) - before
        assert after <= CEILING, f"{after} check_window in 10 minutes: the train spins"
        assert state["status"] == "collecting" and state["batch_size"] == 0, state
        description = await train.describe()
        assert description.run_id == train.result_run_id, "the train relayed: it spun"

        # The request is not carried over: an item that boards waits for its window, it does not leave.
        await train.signal("merged", {"work_item_key": "varga/billing-api#9", "sha": "f9"})
        await temporal_env.sleep(timedelta(minutes=3))
        state = await train.query("status_query")
        assert state["status"] == "collecting", state
        assert state["pending_items"] == ["varga/billing-api#9"], state

        await train.signal("abort", {"by": "test"})
        await train.result()
        history = await train.fetch_history()

    # The history replays against the current code, and is archived for the next ones.
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        target = HISTORIES / "train-empty-departure-S22-29.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_a_departure_on_a_frozen_train_does_not_make_it_spin(
    temporal_env: Any, worker_factory: Any
) -> None:
    """A manual departure on a frozen train is dropped: after the unfreeze, the batch waits its window."""
    looks: list[int] = []
    async with worker_factory(remplacements=_doubles(looks)):
        train = await temporal_env.client.start_workflow(
            "ReleaseTrain",
            {"project_slug": "frozen", "env": "staging", "frozen": True},
            id="train-frozen-staging",
            task_queue="test",
        )
        await train.signal("merged", {"work_item_key": "varga/billing-api#10", "sha": "f10"})
        state = await train.query("status_query")
        assert state["frozen"] and state["batch_size"] == 1, state

        before = len(looks)
        await train.signal("depart_now", {"by": "test"})
        await temporal_env.sleep(timedelta(minutes=10))
        after = len(looks) - before
        assert after <= CEILING, f"{after} check_window in 10 minutes: the frozen train spins"
        description = await train.describe()
        assert description.run_id == train.result_run_id, "the train relayed: it spun"

        # The request was dropped: the unfreeze does not send the batch outside its window.
        await train.signal("unfreeze", {"by": "test"})
        await temporal_env.sleep(timedelta(minutes=3))
        state = await train.query("status_query")
        assert state["status"] == "collecting", state
        assert state["pending_items"] == ["varga/billing-api#10"], state

        await train.signal("abort", {"by": "test"})
        await train.result()
        history = await train.fetch_history()

    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        target = HISTORIES / "train-frozen-departure-S22-29.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_a_hotfix_on_a_frozen_train_waits_without_spinning_and_leaves_at_the_unfreeze(
    temporal_env: Any, worker_factory: Any
) -> None:
    """`express` is kept across the freeze: no spin while frozen, an express departure at the unfreeze."""
    looks: list[int] = []
    async with worker_factory(remplacements=_doubles(looks)):
        train = await temporal_env.client.start_workflow(
            "ReleaseTrain",
            {"project_slug": "hotfix", "env": "staging", "frozen": True},
            id="train-hotfix-staging",
            task_queue="test",
        )
        state = await train.query("status_query")
        before = len(looks)
        await train.signal(
            "merged", {"work_item_key": "varga/billing-api#11", "sha": "f11", "labels": ["hotfix"]}
        )
        await temporal_env.sleep(timedelta(minutes=10))
        after = len(looks) - before
        assert after <= CEILING, f"{after} check_window in 10 minutes: the frozen train spins"
        state = await train.query("status_query")
        assert state["status"] == "collecting" and state["batch_size"] == 1, state

        # The window is still closed: only the kept `express` can send the batch.
        await train.signal("unfreeze", {"by": "test"})
        await temporal_env.sleep(timedelta(minutes=30))
        state = await train.query("status_query")
        assert state["batch_size"] == 0 and state["release_id"] == "rel-1", state

        await train.signal("abort", {"by": "test"})
        await train.result()
