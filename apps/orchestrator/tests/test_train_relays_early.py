"""The train relays itself before its history is too long to replay (S22-27).

On the dev tenant, on 10/10 (0.17.11): histories of ~16,000 events (2.6 MB) took 32 to 34 s to
replay on the orchestrator pod, when the workflow task times out at 10 s. After a
`temporal workflow reset` or a worker restart, every task timed out and every `status_query`
cost a full replay (API 500). The relay of S22-24 came at 15,000 events: too late.

Here, REAL `ReleaseTrain`s: at rest with the DEFAULT threshold, after a departure, after an
`auto_sync` sync; and a run from before S22-27 replayed against the lowered threshold.
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
#: What a history can carry while staying replayable well under the 10 s of the workflow task
#: (~2 ms per event on the pod, measured on 10/10).
REPLAYABLE = 2_500


def _at_rest() -> dict[str, Any]:
    """A `release_train` train in front of a closed window: nothing leaves, the train loops."""
    from temporalio import activity

    @activity.defn(name="load_train_config")
    async def load_train_config(payload: dict[str, Any]) -> dict[str, Any]:
        return {"mode": "release_train", "batch_min": 1, "batch_max": 8}

    @activity.defn(name="check_window")
    async def check_window(payload: dict[str, Any]) -> dict[str, Any]:
        return {"open": False, "due": False, "wait_seconds": 60, "next_departure": None}

    return {"load_train_config": load_train_config, "check_window": check_window}


def _departing() -> dict[str, Any]:
    """A train whose window is open and whose every departure step succeeds."""
    from temporalio import activity

    @activity.defn(name="load_train_config")
    async def load_train_config(payload: dict[str, Any]) -> dict[str, Any]:
        return {"mode": "release_train", "batch_min": 1, "batch_max": 8, "soak_minutes": 1}

    @activity.defn(name="check_window")
    async def check_window(payload: dict[str, Any]) -> dict[str, Any]:
        return {"open": True, "due": True, "wait_seconds": 60, "next_departure": None}

    @activity.defn(name="create_release")
    async def create_release(payload: dict[str, Any]) -> dict[str, Any]:
        return {"release_id": f"rel-{payload['batch_no']}"}

    def _succeeding(name: str) -> Any:
        @activity.defn(name=name)
        async def step(payload: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True}

        return step

    steps = ["promote", "run_smoke", "soak", "apply_terraform", "promote_canary_step", "verify_prod"]
    return {
        "load_train_config": load_train_config,
        "check_window": check_window,
        "create_release": create_release,
        **{name: _succeeding(name) for name in [*steps, "finish_release"]},
    }


def _in_auto_sync() -> dict[str, Any]:
    """A healthy `auto_sync` environment: every ticket is confirmed at first sight."""
    from temporalio import activity

    @activity.defn(name="load_train_config")
    async def load_train_config(payload: dict[str, Any]) -> dict[str, Any]:
        return {"mode": "auto_sync"}

    @activity.defn(name="confirm_auto_sync")
    async def confirm_auto_sync(payload: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}

    return {"load_train_config": load_train_config, "confirm_auto_sync": confirm_auto_sync}


async def _first_run_relays(handle: Any, timeout: float = 60) -> str:
    """Waits for the end of the FIRST run: a relay, not a failure nor an endless wait."""
    from temporalio.client import WorkflowContinuedAsNewError

    with pytest.raises(WorkflowContinuedAsNewError) as relay:
        await asyncio.wait_for(handle.result(follow_runs=False), timeout=timeout)
    return str(relay.value.new_execution_run_id)


def _markers(history: Any) -> list[str]:
    """The `patched` markers recorded in the history, in order."""
    from temporalio.api.common.v1 import Payload

    ids: list[str] = []
    for event in history.events:
        if event.HasField("marker_recorded_event_attributes"):
            details = event.marker_recorded_event_attributes.details
            if "patch-data" in details:
                payload: Payload = details["patch-data"].payloads[0]
                ids.append(json.loads(payload.data)["id"])
    return ids


def _activities(history: Any) -> list[str]:
    return [
        e.activity_task_scheduled_event_attributes.activity_type.name
        for e in history.events
        if e.HasField("activity_task_scheduled_event_attributes")
    ]


def _relay_input(history: Any) -> dict[str, Any]:
    relay = history.events[-1]
    assert relay.HasField("workflow_execution_continued_as_new_event_attributes")
    data = relay.workflow_execution_continued_as_new_event_attributes.input.payloads[0].data
    continued_input: dict[str, Any] = json.loads(data)
    return continued_input


async def test_a_train_at_rest_relays_before_being_too_long_to_replay(
    temporal_env: Any, worker_factory: Any
) -> None:
    """The DEFAULT threshold: no `relay_threshold` in the input, like the tenant's trains."""
    from choregos_orchestrator.workflows.train import (
        IDLE_TRAIN_CONTINUES_AS_NEW,
        TRAIN_CONTINUES_AS_NEW_EARLY,
    )

    async with worker_factory(replacements=_at_rest()):
        train = await temporal_env.client.start_workflow(
            "ReleaseTrain",
            {"project_slug": "rest", "env": "staging"},
            id="train-rest-short",
            task_queue="test",
        )
        # Without the fix, the relay waits for 15,000 events: the wait expires, or the history
        # exceeds what a workflow task can replay.
        new_run = await _first_run_relays(train)
        first = temporal_env.client.get_workflow_handle("train-rest-short", run_id=train.result_run_id)
        history = await first.fetch_history()
        next_run = temporal_env.client.get_workflow_handle("train-rest-short", run_id=new_run)
        assert (await next_run.query("status_query"))["status"] == "collecting"
        await next_run.signal("abort", {"by": "test"})
        await next_run.result()

    assert len(history.events) <= REPLAYABLE, f"relayed at {len(history.events)} events"
    markers = _markers(history)
    assert TRAIN_CONTINUES_AS_NEW_EARLY in markers and IDLE_TRAIN_CONTINUES_AS_NEW not in markers, markers
    assert "relay_threshold" not in _relay_input(history)
    assert "seuil_de_relais" not in _relay_input(history)  # lang: fr-ok (the former input key)


async def test_a_train_relays_right_after_its_departure(temporal_env: Any, worker_factory: Any) -> None:
    """A departure adds dozens of events: past the threshold, the train relays at once, without
    waiting to go back through the collection (where the relay at rest would have done it before S22-27)."""
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from choregos_orchestrator.workflows.train import TRAIN_CONTINUES_AS_NEW_EARLY
    from temporalio.worker import Replayer

    async with worker_factory(replacements=_departing()):
        train = await temporal_env.client.start_workflow(
            "ReleaseTrain",
            {"project_slug": "departure", "env": "prod", "relay_threshold": 40},
            id="train-departure-prod",
            task_queue="test",
            start_signal="merged",
            start_signal_args=[{"work_item_key": "varga/billing-api#9", "sha": "a9"}],
        )
        new_run = await _first_run_relays(train)
        first = temporal_env.client.get_workflow_handle("train-departure-prod", run_id=train.result_run_id)
        history = await first.fetch_history()
        next_run = temporal_env.client.get_workflow_handle("train-departure-prod", run_id=new_run)
        await next_run.signal("abort", {"by": "test"})
        await next_run.result()

    activities = _activities(history)
    assert activities.count("check_window") == 1, activities
    assert activities[-1] == "finish_release", "the relay follows the departure, not a new collection"
    assert TRAIN_CONTINUES_AS_NEW_EARLY in _markers(history)
    continued_input = _relay_input(history)
    assert continued_input["carried_items"] == [] and continued_input["batch_no"] == 2

    # The history of the relay replays against the current code, and is archived for those to come.
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        target = HISTORIES / "train-relays-early-S22-27.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_an_auto_sync_train_relays(temporal_env: Any, worker_factory: Any) -> None:
    from choregos_orchestrator.workflows.train import TRAIN_CONTINUES_AS_NEW_EARLY

    async with worker_factory(replacements=_in_auto_sync()):
        train = await temporal_env.client.start_workflow(
            "ReleaseTrain",
            {"project_slug": "sync", "env": "dev", "relay_threshold": 10},
            id="train-sync-dev",
            task_queue="test",
            start_signal="merged",
            start_signal_args=[{"work_item_key": "varga/billing-api#10", "sha": "b10"}],
        )
        # Without the fix, the sync relays only at 15,000: the run waits for a ticket, endlessly.
        new_run = await _first_run_relays(train, timeout=30)
        first = temporal_env.client.get_workflow_handle("train-sync-dev", run_id=train.result_run_id)
        history = await first.fetch_history()
        next_run = temporal_env.client.get_workflow_handle("train-sync-dev", run_id=new_run)
        for _ in range(100):  # the next run rereads its configuration before saying `auto_sync`
            state = await next_run.query("status_query")
            if state["status"] == "auto_sync":
                break
            await asyncio.sleep(0.1)
        assert state["status"] == "auto_sync" and state["batch_size"] == 0, state
        await next_run.signal("abort", {"by": "test"})
        await next_run.result()

    assert _activities(history)[-1] == "confirm_auto_sync", "English name (S34-02)"
    assert TRAIN_CONTINUES_AS_NEW_EARLY in _markers(history)
    assert _relay_input(history)["batch_no"] == 2


async def test_a_run_from_before_replays_against_the_lowered_threshold(
    temporal_env: Any, worker_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run from before S22-27, relayed at rest at the former threshold, replays against the new one.

    At scale: the former threshold at 300, the new one at 100. The "before" run is recorded with a
    new threshold out of reach: it therefore relays at 300 under the old marker, like 0.17.11 at
    15,000. Then we show that this same history would NOT replay if the threshold had merely been lowered,
    without a new marker: this is what made `TRAIN_CONTINUES_AS_NEW_EARLY` necessary."""
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from choregos_orchestrator.workflows import train as module
    from temporalio.worker import Replayer

    monkeypatch.setattr(module, "RELAY_THRESHOLD", 10**9)
    monkeypatch.setattr(module, "FORMER_RELAY_THRESHOLD", 300)
    async with worker_factory(replacements=_at_rest()):
        train = await temporal_env.client.start_workflow(
            "ReleaseTrain",
            {"project_slug": "before", "env": "staging"},
            id="train-before-staging",
            task_queue="test",
        )
        new_run = await _first_run_relays(train)
        first = temporal_env.client.get_workflow_handle("train-before-staging", run_id=train.result_run_id)
        history = await first.fetch_history()
        next_run = temporal_env.client.get_workflow_handle("train-before-staging", run_id=new_run)
        await next_run.signal("abort", {"by": "test"})
        await next_run.result()
    assert 300 < len(history.events) < 400
    markers = _markers(history)
    assert (
        module.IDLE_TRAIN_CONTINUES_AS_NEW in markers and module.TRAIN_CONTINUES_AS_NEW_EARLY not in markers
    )

    # Today's code, at scale: the new marker is asked from 100, returns `False`, and
    # the run keeps its former relay.
    monkeypatch.setattr(module, "RELAY_THRESHOLD", 100)
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)

    # The threshold merely lowered (the old marker asked from 100): determinism gap.
    monkeypatch.setattr(module, "FORMER_RELAY_THRESHOLD", 100)
    replay = await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history, raise_on_replay_failure=False)
    assert replay.replay_failure is not None
    assert "TMPRL1100" in str(replay.replay_failure)
