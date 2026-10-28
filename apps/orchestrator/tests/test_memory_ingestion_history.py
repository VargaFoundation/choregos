"""`MemoryIngestion` (S47-23): one ingestion cycle of the project's sources, then the `stop` signal.
Its history replays (`tests/replay`), and the replay suite refuses a workflow that has none."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from temporalio import activity

HISTORY = (
    Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories" / "memory-ingestion-S47-23.json"
)
WORKFLOW_ID = "mem-replay"
PAYLOAD = {"project_slug": "replay", "sources": ["closed_issues", "adr"]}


async def play() -> tuple[dict[str, Any], dict[str, list[Any]], Any]:
    """The workflow against stub activities: its result, what each activity was asked, its history."""
    from choregos_orchestrator.testing import workers_repartis
    from choregos_orchestrator.workflows.memory import MemoryIngestion
    from temporalio.testing import WorkflowEnvironment

    asked: dict[str, list[Any]] = {"ingest": []}

    @activity.defn(name="ingest_sources")
    async def ingest_sources(payload: dict[str, Any]) -> dict[str, Any]:
        asked["ingest"].append(payload)
        return {"facts": 3}

    environment = await WorkflowEnvironment.start_time_skipping()
    try:
        async with workers_repartis(
            environment.client, activites=[ingest_sources], workflows=[MemoryIngestion]
        ):
            handle = await environment.client.start_workflow(
                "MemoryIngestion", PAYLOAD, id=WORKFLOW_ID, task_queue="test"
            )
            # The test server's clock stands still until the result is awaited: the signal ends the
            # first wait, so exactly one cycle runs, and then the loop ends.
            await handle.signal("stop", {})
            result = await handle.result()
            history = await handle.fetch_history()
    finally:
        await environment.shutdown()
    return result, asked, history


async def test_one_cycle_ingests_the_sources_and_stop_ends_the_loop() -> None:
    result, asked, _ = await play()
    assert result == {"cycles": 1, "ingested": 3}
    assert asked["ingest"] == [{"project_slug": "replay", "sources": ["closed_issues", "adr"]}]


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
