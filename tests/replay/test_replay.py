"""Replay archived histories: a logic change must not break the runs in flight.

This is the counterpart of `workflow.patched()`: the CI replays the recorded histories
against the current code. If a workflow makes a different decision, the replay fails —
before that happens in production.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

HISTORIES = Path(__file__).parent / "histories"


def history_files() -> list[Path]:
    return sorted(HISTORIES.glob("*.json"))


def recorded_workflow_types() -> set[str]:
    """The workflow type each archived history starts with."""
    return {
        json.loads(path.read_text(encoding="utf-8"))["events"][0]["workflowExecutionStartedEventAttributes"][
            "workflowType"
        ]["name"]
        for path in history_files()
    }


def registered_workflow_types() -> set[str]:
    """The name Temporal knows each registered workflow class by (`@workflow.defn(name=...)`)."""
    from choregos_orchestrator.workflows import ALL_WORKFLOWS

    names: set[str] = set()
    for workflow_class in ALL_WORKFLOWS:
        definition = getattr(workflow_class, "__temporal_workflow_definition", None)
        assert definition is not None, f"{workflow_class!r} is in ALL_WORKFLOWS but is no workflow"
        names.add(definition.name)
    return names


def workflows_without_history(recorded: Iterable[str], expected: Iterable[str]) -> set[str]:
    """The expected workflow types that no recorded history starts with."""
    return set(expected) - set(recorded)


def test_every_workflow_has_a_history() -> None:
    """A workflow with no archived history is a workflow whose change nothing replays: record one
    (`make replay-record`, or the `__main__` block of its `apps/orchestrator/tests/test_*_history.py`)."""
    missing = workflows_without_history(recorded_workflow_types(), registered_workflow_types())
    assert not missing, f"no archived history in tests/replay/histories for: {sorted(missing)}"


def test_a_workflow_without_a_history_is_reported() -> None:
    """The guard's own logic, on synthetic types: what has no history is named, what has one is not."""
    recorded = ["WorkflowInterpreter", "ReleaseTrain", "ReleaseTrain"]
    expected = ["WorkflowInterpreter", "ReleaseTrain", "AgentTrial", "MemoryIngestion"]
    assert workflows_without_history(recorded, expected) == {"AgentTrial", "MemoryIngestion"}
    assert workflows_without_history(expected, expected) == set()
    assert workflows_without_history([], expected) == set(expected)


@pytest.mark.skipif(not history_files(), reason="no archived history (see `make replay-record`)")
@pytest.mark.parametrize("path", history_files(), ids=lambda path: path.stem)
async def test_replay_history(path: Path) -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.client import WorkflowHistory
    from temporalio.worker import Replayer

    payload = json.loads(path.read_text(encoding="utf-8"))
    history = WorkflowHistory.from_json(payload.get("workflowId", path.stem), payload)
    replayer = Replayer(workflows=ALL_WORKFLOWS)
    await replayer.replay_workflow(history)


async def test_record_and_replay_roundtrip(tmp_path: Path) -> None:
    """Record a fresh history then replay it: the loop is verifiable in CI."""
    from choregos_orchestrator.testing import workers_repartis
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from choregos_orchestrator.workflows.train import ReleaseTrain
    from temporalio.client import WorkflowHistory
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Replayer

    environment = await WorkflowEnvironment.start_time_skipping()
    try:
        # One worker per queue: `notify` and `enqueue_merge` are scheduled on `tracker`, not on
        # the workflow's queue. A single worker would hide the split the history must carry,
        # and the replayed history would be that of a deployment that does not exist.
        async with workers_repartis(
            environment.client,
            activites=_stub_activities(),
            workflows=[ReleaseTrain],
            file_du_workflow="replay",
        ):
            handle = await environment.client.start_workflow(
                "ReleaseTrain",
                {"project_slug": "replay", "env": "dev"},
                id="train-replay-dev",
                task_queue="replay",
            )
            await handle.signal("abort", {"by": "test"})
            await handle.result()
            history = await handle.fetch_history()
    finally:
        await environment.shutdown()

    archived = tmp_path / "train.json"
    archived.write_text(history.to_json(), encoding="utf-8")

    replayer = Replayer(workflows=ALL_WORKFLOWS)
    await replayer.replay_workflow(
        WorkflowHistory.from_json("train-replay-dev", json.loads(archived.read_text()))
    )


def _stub_activities() -> list[Any]:
    """Neutral activities: the replay does not replay effects, only decisions."""
    from temporalio import activity

    @activity.defn(name="load_train_config")
    async def load_train_config(payload: dict[str, Any]) -> dict[str, Any]:
        return {"mode": "auto_sync"}

    return [load_train_config]
