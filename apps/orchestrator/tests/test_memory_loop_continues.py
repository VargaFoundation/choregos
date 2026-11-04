"""`MemoryIngestion` past `max_cycles` continues as new instead of returning (S51-02).

A completed `mem-<slug>` cannot be started again under its id (the start reuses only a FAILED one), so
a loop that returned after its 96 cycles left the project's memory unfed until someone noticed. Its
history replays (`tests/replay`); the S47-23 history (ended by `stop`) still does.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from temporalio import activity

HISTORY = (
    Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories" / "memory-ingestion-S51-02.json"
)
WORKFLOW_ID = "mem-replay-continues"
PAYLOAD = {"project_slug": "replay", "sources": ["closed_issues"], "max_cycles": 1}


async def play() -> tuple[str, dict[str, Any] | None, dict[str, list[Any]], Any]:
    """One cycle of a loop whose `max_cycles` is 1: how its first run ended, the input of the next run,
    what the activity was asked, and the first run's history."""
    from choregos_orchestrator.testing import wait_for_event, workers_by_queue
    from choregos_orchestrator.workflows.memory import MemoryIngestion
    from temporalio.api.enums.v1 import EventType
    from temporalio.client import WorkflowContinuedAsNewError
    from temporalio.converter import default
    from temporalio.testing import WorkflowEnvironment

    asked: dict[str, list[Any]] = {"ingest": []}

    @activity.defn(name="ingest_sources")
    async def ingest_sources(payload: dict[str, Any]) -> dict[str, Any]:
        asked["ingest"].append(payload)
        return {"facts": 2}

    environment = await WorkflowEnvironment.start_time_skipping()
    try:
        async with workers_by_queue(
            environment.client, activities=[ingest_sources], workflows=[MemoryIngestion]
        ):
            handle = await environment.client.start_workflow(
                "MemoryIngestion", PAYLOAD, id=WORKFLOW_ID, task_queue="test"
            )
            # `reimport` once the loop waits on its first timer: the cycle runs at once, deterministically.
            await wait_for_event(handle, EventType.EVENT_TYPE_TIMER_STARTED)
            await handle.signal("reimport", {"sources": ["adr"]})
            ended = "completed"
            try:
                await handle.result(follow_runs=False)
            except WorkflowContinuedAsNewError:
                ended = "continued_as_new"
            # The FIRST run's history: the handle alone would read the run it continued into.
            first = environment.client.get_workflow_handle(WORKFLOW_ID, run_id=handle.first_execution_run_id)
            history = await first.fetch_history()
            next_input: dict[str, Any] | None = None
            for event in history.events:
                if event.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_CONTINUED_AS_NEW:
                    attributes = event.workflow_execution_continued_as_new_event_attributes
                    next_input = default().payload_converter.from_payloads(attributes.input.payloads)[0]
            if ended == "continued_as_new":
                await environment.client.get_workflow_handle(WORKFLOW_ID).signal("stop", {})
    finally:
        await environment.shutdown()
    return ended, next_input, asked, history


async def test_a_loop_past_its_cycles_continues_as_new_with_the_same_input() -> None:
    ended, next_input, asked, _ = await play()
    assert ended == "continued_as_new", "a returned loop could never start again under `mem-<slug>`"
    assert next_input == {**PAYLOAD, "last_report_week": 0}
    assert asked["ingest"][0] == {"project_slug": "replay", "sources": ["adr"]}


async def test_the_archived_history_replays() -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.api.enums.v1 import EventType
    from temporalio.client import WorkflowHistory
    from temporalio.worker import Replayer

    _, _, _, history = await play()
    replayer = Replayer(workflows=ALL_WORKFLOWS)
    await replayer.replay_workflow(history)
    archived = json.loads(HISTORY.read_text(encoding="utf-8"))
    await replayer.replay_workflow(WorkflowHistory.from_json(WORKFLOW_ID, archived))
    assert [EventType.Name(event.event_type) for event in history.events] == [
        event["eventType"] for event in archived["events"]
    ], "the fresh run takes the archived path: timer, `reimport`, one ingestion, continued as new"


if __name__ == "__main__":  # `python -m` from the repository root: records the archived history again
    import asyncio

    async def _record() -> None:
        _, _, _, history = await play()
        HISTORY.write_text(history.to_json(), encoding="utf-8")

    asyncio.run(_record())
