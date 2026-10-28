"""`AgentTrial` (S47-23): prepare the stage, start the run, await it, collect its spend, record its
outcome. Its history replays (`tests/replay`), and the replay suite refuses a workflow that has none."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from temporalio import activity

HISTORY = Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories" / "agent-trial-S47-23.json"
WORKFLOW_ID = "trial-wi-1"
SPEC = {"instructions": "Sort the ticket by urgency.", "model": "profile:cheap"}
PAYLOAD = {"project_id": "project-1", "work_item_id": "wi-1", "spec": SPEC, "budget_usd": 0.3}
RUN_ID = "wi-1-trial-1"
RESULT = {"status": "done", "summary": "sorted: urgent"}


async def play() -> tuple[dict[str, Any], dict[str, list[Any]], Any]:
    """The workflow against stub activities: its result, what each activity was asked, its history."""
    from choregos_orchestrator.testing import workers_repartis
    from choregos_orchestrator.workflows.trial import AgentTrial
    from temporalio.testing import WorkflowEnvironment

    asked: dict[str, list[Any]] = {"prepare": [], "start": [], "await": [], "spend": [], "record": []}

    @activity.defn(name="prepare_stage")
    async def prepare_stage(payload: dict[str, Any]) -> dict[str, Any]:
        asked["prepare"].append(payload)
        stage_input = {"run_id": RUN_ID, "budget": {"usd": 0.3, "max_minutes": 10, "max_turns": 6}}
        return {"run_id": RUN_ID, "stage_input": stage_input}

    @activity.defn(name="start_run")
    async def start_run(payload: dict[str, Any]) -> dict[str, Any]:
        asked["start"].append(payload)
        return {"run_id": RUN_ID, "capabilities": []}

    @activity.defn(name="await_run")
    async def await_run(payload: dict[str, Any]) -> dict[str, Any]:
        asked["await"].append(payload)
        return {"status": "finished", "result": RESULT}

    @activity.defn(name="collect_spend")
    async def collect_spend(payload: dict[str, Any]) -> dict[str, Any]:
        asked["spend"].append(payload)
        return {"cost_usd": 0.12}

    @activity.defn(name="record_run_outcome")
    async def record_run_outcome(payload: dict[str, Any]) -> dict[str, Any]:
        asked["record"].append(payload)
        return {"recorded": True}

    environment = await WorkflowEnvironment.start_time_skipping()
    try:
        async with workers_repartis(
            environment.client,
            activites=[prepare_stage, start_run, await_run, collect_spend, record_run_outcome],
            workflows=[AgentTrial],
        ):
            handle = await environment.client.start_workflow(
                "AgentTrial", PAYLOAD, id=WORKFLOW_ID, task_queue="test"
            )
            result = await handle.result()
            history = await handle.fetch_history()
    finally:
        await environment.shutdown()
    return result, asked, history


async def test_a_trial_prepares_starts_awaits_collects_the_spend_and_records_the_outcome() -> None:
    from temporalio.api.enums.v1 import EventType

    result, asked, history = await play()
    assert result == {
        "run_id": RUN_ID,
        "status": "done",
        "summary": "sorted: urgent",
        "cost_usd": 0.12,
        "budget_usd": 0.3,
    }
    (prepared,) = asked["prepare"]
    assert prepared["trial_spec"] == SPEC and prepared["trial_budget_usd"] == 0.3
    assert prepared["transition_id"] == "trial", "the trial's run bears the trial transition"
    assert asked["await"][0]["run_id"] == RUN_ID and asked["await"][0]["timeout_minutes"] == 20
    assert asked["spend"] == [{"project_id": "project-1", "run_id": RUN_ID}]
    assert asked["record"] == [{"project_id": "project-1", "run_id": RUN_ID, "result": RESULT}]
    scheduled = [
        event.activity_task_scheduled_event_attributes.activity_type.name
        for event in history.events
        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
    ]
    assert scheduled == ["prepare_stage", "start_run", "await_run", "collect_spend", "record_run_outcome"]


async def test_the_archived_history_replays() -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.client import WorkflowHistory
    from temporalio.worker import Replayer

    _, _, history = await play()
    replayer = Replayer(workflows=ALL_WORKFLOWS)
    await replayer.replay_workflow(history)
    archived = json.loads(HISTORY.read_text(encoding="utf-8"))
    await replayer.replay_workflow(WorkflowHistory.from_json(WORKFLOW_ID, archived))


if __name__ == "__main__":  # `python -m` from the repository root: records the archived history again
    import asyncio

    async def _record() -> None:
        _, _, history = await play()
        HISTORY.write_text(history.to_json(), encoding="utf-8")

    asyncio.run(_record())
