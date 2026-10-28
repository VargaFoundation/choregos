"""`FindingsTriage` (S47-23): a finding is triaged into a ticket or counted as a duplicate, until the
`stop` signal. Its history replays (`tests/replay`), and the replay suite refuses a workflow that has
none."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from temporalio import activity

HISTORY = (
    Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories" / "findings-triage-S47-23.json"
)
WORKFLOW_ID = "findings-replay"
PAYLOAD = {"project_slug": "replay", "pending": [{"finding_id": "finding-1"}]}
SIGNALLED = {"finding_id": "finding-2"}
OUTCOMES = {
    "finding-1": {"status": "created", "work_item_key": "acme/app#41", "work_item_id": "wi-41"},
    "finding-2": {"status": "duplicate", "duplicate_of": "finding-1"},
}


async def play() -> tuple[dict[str, Any], dict[str, list[Any]], Any]:
    """The workflow against stub activities: its result, what each activity was asked, its history."""
    from choregos_orchestrator.testing import workers_repartis
    from choregos_orchestrator.workflows.findings import FindingsTriage
    from temporalio.testing import WorkflowEnvironment

    asked: dict[str, list[Any]] = {"triage": []}

    @activity.defn(name="triage_finding")
    async def triage_finding(payload: dict[str, Any]) -> dict[str, Any]:
        asked["triage"].append(payload)
        return dict(OUTCOMES[payload["finding_id"]])

    environment = await WorkflowEnvironment.start_time_skipping()
    try:
        async with workers_repartis(
            environment.client, activites=[triage_finding], workflows=[FindingsTriage]
        ):
            handle = await environment.client.start_workflow(
                "FindingsTriage", PAYLOAD, id=WORKFLOW_ID, task_queue="test"
            )
            # Signals are delivered in order: the second finding is queued before `stop` is seen,
            # and the workflow drains its queue before it leaves its loop.
            await handle.signal("finding", SIGNALLED)
            await handle.signal("stop", {})
            result = await handle.result()
            history = await handle.fetch_history()
    finally:
        await environment.shutdown()
    return result, asked, history


async def test_a_finding_is_filed_a_duplicate_is_counted_and_stop_ends_the_loop() -> None:
    from temporalio.api.enums.v1 import EventType

    result, asked, history = await play()
    assert result == {"processed": 2, "created": ["acme/app#41"], "duplicates": 1}
    assert asked["triage"] == [
        {"project_slug": "replay", "finding_id": "finding-1"},
        {"project_slug": "replay", "finding_id": "finding-2"},
    ]
    started = [
        event
        for event in history.events
        if event.event_type == EventType.EVENT_TYPE_START_CHILD_WORKFLOW_EXECUTION_INITIATED
    ]
    assert started == [], "no outcome was agent_ready, so no work item workflow was started"


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
