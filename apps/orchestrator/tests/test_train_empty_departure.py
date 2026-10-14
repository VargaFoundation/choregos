"""A departure requested on a train without a batch does not make it spin (#370, S22-29).

Before the fix, `depart_now` raised `depart_requested`, which only `_depart` resets; but `_collect`
does not depart without a batch, and its wait returned at once (the condition was already true): the
train chained `check_window` activities without a pause until the relay (~2,000 events since S22-27).

Here, a REAL empty `ReleaseTrain` in front of a closed window (`wait_seconds` = 60) receives
`depart_now`, then time moves 10 minutes forward: the `check_window` activities are counted.
"""

from __future__ import annotations

import json
import os
import pathlib
from datetime import timedelta
from typing import Any

import pytest

pytestmark = pytest.mark.asyncio

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"
#: 10 minutes at one look per minute: ~11 looks. The empty spin made hundreds.
CEILING = 15


def _doubles(looks: list[int]) -> dict[str, Any]:
    """A `release_train` train in front of a closed window, counting its `check_window` calls."""
    from temporalio import activity

    @activity.defn(name="load_train_config")
    async def load_train_config(payload: dict[str, Any]) -> dict[str, Any]:
        return {"mode": "release_train", "batch_min": 1, "batch_max": 8}

    @activity.defn(name="check_window")
    async def check_window(payload: dict[str, Any]) -> dict[str, Any]:
        looks.append(1)
        return {"open": False, "due": False, "wait_seconds": 60, "next_departure": None}

    return {"load_train_config": load_train_config, "check_window": check_window}


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
    if os.environ.get("CHOREGOS_ARCHIVER_HISTORIQUE"):
        target = HISTORIES / "train-empty-departure-S22-29.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")
