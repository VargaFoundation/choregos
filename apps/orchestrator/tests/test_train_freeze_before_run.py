"""A freeze delivered before the body of `ReleaseTrain.run` is kept (#763, S22-36b).

A signal sent with the start (`start_signal`) or right after it is handled BEFORE `run` executes.
`run` then did `self.frozen = params.frozen`, which overwrote the freeze while its reason stayed:
`frozen=False` with a `freeze_reason`, and a frozen train could depart. Here the race is made
deterministic: the freeze IS the start signal, so it is always handled before the body.
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


def _doubles(created: list[int]) -> dict[str, Any]:
    """A `release_train` train in front of an OPEN window; every step of a departure succeeds."""
    from temporalio import activity

    @activity.defn(name="create_release")
    async def create_release(payload: dict[str, Any]) -> dict[str, Any]:
        created.append(1)
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
        return {"open": True, "due": True, "wait_seconds": 60, "next_departure": None}

    return {
        "load_train_config": load_train_config,
        "check_window": check_window,
        "create_release": create_release,
        **{name: _succeeds(name) for name in [*steps, "finish_release"]},
    }


async def test_a_freeze_delivered_with_the_start_blocks_the_departure(
    temporal_env: Any, worker_factory: Any
) -> None:
    created: list[int] = []
    async with worker_factory(remplacements=_doubles(created)):
        train = await temporal_env.client.start_workflow(
            "ReleaseTrain",
            {"project_slug": "early", "env": "staging"},
            id="train-early-freeze",
            task_queue="test",
            start_signal="freeze",
            start_signal_args=[{"reason": "incident", "by": "marie"}],
        )
        await train.signal("merged", {"work_item_key": "varga/billing-api#20", "sha": "f20"})
        await train.signal("depart_now", {"by": "test"})
        await temporal_env.sleep(timedelta(minutes=5))
        state = await train.query("status_query")
        assert state["frozen"] is True and state["freeze_reason"] == "incident", state
        assert state["batch_size"] == 1 and state["status"] == "collecting", state
        assert not created, "a frozen train departed"

        await train.signal("abort", {"by": "test"})
        await train.result()
        history = await train.fetch_history()

    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        target = HISTORIES / "train-freeze-before-run-S22-36b.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_a_history_recorded_before_the_fix_still_replays() -> None:
    """Recorded on the code of #763 (no marker): the old reading, `frozen` overwritten, replays."""
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.client import WorkflowHistory
    from temporalio.worker import Replayer

    path = HISTORIES / "train-freeze-before-run-old-code-S22-36b.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    history = WorkflowHistory.from_json("train-early-freeze", payload)
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
