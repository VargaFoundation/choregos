"""The train at rest relays itself before Temporal kills it (S22-24).

On the dev tenant, on 10/10: `train-dev-staging` carried 15,814 events after 23 h without a single
departure. `_collect` looped (a `check_window` activity and a timer per turn), and the only
`continue_as_new` followed a departure. At 51,200 events, Temporal terminates the workflow: the train
dies, and the tickets that board it wait forever.

Here, a REAL `ReleaseTrain` waits in front of a closed window; the relay threshold is lowered by its
input (`relay_threshold`) so as not to produce thousands of events.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from typing import Any

import pytest

from .conftest import recording_history

pytestmark = pytest.mark.asyncio

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"
THRESHOLD = 60


def _doubles() -> dict[str, Any]:
    """A `release_train` train in front of a closed window: nothing leaves, the train loops."""
    from temporalio import activity

    @activity.defn(name="load_train_config")
    async def load_train_config(payload: dict[str, Any]) -> dict[str, Any]:
        return {"mode": "release_train", "batch_min": 1, "batch_max": 8}

    @activity.defn(name="check_window")
    async def check_window(payload: dict[str, Any]) -> dict[str, Any]:
        return {"open": False, "due": False, "wait_seconds": 60, "next_departure": None}

    return {"load_train_config": load_train_config, "check_window": check_window}


async def _first_run_relays(handle: Any) -> str:
    """Waits for the end of the FIRST run: a relay, not a failure nor an endless loop."""
    from temporalio.client import WorkflowContinuedAsNewError

    with pytest.raises(WorkflowContinuedAsNewError) as relay:
        # Without the fix, this run never ends: the wait expires and the test goes red.
        await asyncio.wait_for(handle.result(follow_runs=False), timeout=60)
    return str(relay.value.new_execution_run_id)


async def test_an_empty_train_at_rest_relays(temporal_env: Any, worker_factory: Any) -> None:
    async with worker_factory(replacements=_doubles()):
        train = await temporal_env.client.start_workflow(
            "ReleaseTrain",
            {"project_slug": "repos", "env": "staging", "relay_threshold": THRESHOLD},
            id="train-repos-staging",
            task_queue="test",
        )
        new_run = await _first_run_relays(train)
        assert new_run != train.result_run_id

        next_run = temporal_env.client.get_workflow_handle("train-repos-staging")
        description = await next_run.describe()
        assert description.run_id == new_run
        state = await next_run.query("status_query")
        assert state["status"] == "collecting" and state["batch_size"] == 0, state
        await next_run.signal("abort", {"by": "test"})
        await next_run.result()


async def test_a_batch_waiting_for_its_window_passes_to_the_next_run(
    temporal_env: Any, worker_factory: Any
) -> None:
    """The batch is not lost at the relay: it waits for its window in the next run, number included."""
    async with worker_factory(replacements=_doubles()):
        train = await temporal_env.client.start_workflow(
            "ReleaseTrain",
            {"project_slug": "repos", "env": "prod", "batch_no": 3, "relay_threshold": THRESHOLD},
            id="train-repos-prod",
            task_queue="test",
        )
        await train.signal("merged", {"work_item_key": "varga/billing-api#7", "sha": "f7"})
        new_run = await _first_run_relays(train)
        first_run = temporal_env.client.get_workflow_handle("train-repos-prod", run_id=train.result_run_id)
        history = await first_run.fetch_history()

        next_run = temporal_env.client.get_workflow_handle("train-repos-prod", run_id=new_run)
        state = await next_run.query("status_query")
        assert state["status"] == "collecting", state
        assert state["pending_items"] == ["varga/billing-api#7"], "the relay lost the batch"
        await next_run.signal("abort", {"by": "test"})
        await next_run.result()

    relay = history.events[-1]
    assert relay.HasField("workflow_execution_continued_as_new_event_attributes")
    continued_input = json.loads(
        relay.workflow_execution_continued_as_new_event_attributes.input.payloads[0].data
    )
    assert continued_input["carried_items"] == [{"work_item_key": "varga/billing-api#7", "sha": "f7"}]
    assert continued_input["batch_no"] == 3 and continued_input["relay_threshold"] == THRESHOLD

    # The history of the relay replays against the current code, and is archived for those to come.
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        target = HISTORIES / "train-at-rest-S22-24.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")
