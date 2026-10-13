"""`KnowledgeSync` (S33-04): a loop per knowledge source — a pass, an interval or `sync_now`, an end
when the source is gone; a failing pass does not end it. Its history replays (`tests/replay`)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

HISTORY = (
    Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories" / "knowledge-sync-S33-04.json"
)


def scripted(answers: list[Any]) -> Any:
    calls: list[dict[str, Any]] = []

    @activity.defn(name="sync_knowledge_source")
    async def sync_knowledge_source(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(payload)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return dict(answer)

    sync_knowledge_source.calls = calls  # type: ignore[attr-defined]
    return sync_knowledge_source


async def run_loop(answers: list[Any], *, wake: bool = True) -> tuple[dict[str, Any], Any, Any]:
    from choregos_orchestrator.workflows.knowledge import KnowledgeSync
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    stub = scripted(answers)
    environment = await WorkflowEnvironment.start_time_skipping()
    try:
        async with Worker(
            environment.client, task_queue="test", workflows=[KnowledgeSync], activities=[stub]
        ):
            handle = await environment.client.start_workflow(
                "KnowledgeSync",
                {"org": "varga", "source_id": "src-1", "interval_seconds": 600},
                id="knowledge-sync-src-1",
                task_queue="test",
                execution_timeout=timedelta(days=1),
            )
            if wake:
                await handle.signal("sync_now", {})
            result = await handle.result()
            history = await handle.fetch_history()
    finally:
        await environment.shutdown()
    return result, stub, history


async def test_the_loop_syncs_until_its_source_is_gone() -> None:
    result, stub, _ = await run_loop(
        [{"status": "synced", "added": 1, "interval_seconds": 900}, {"status": "gone"}]
    )
    assert result == {"passes": 2, "gone": True}
    assert stub.calls == [{"org": "varga", "source_id": "src-1"}] * 2


async def test_a_failing_pass_does_not_end_the_loop() -> None:
    failure = ApplicationError("graph unreachable", non_retryable=True)
    result, stub, _ = await run_loop([failure, {"status": "gone"}], wake=False)
    assert result == {"passes": 2, "gone": True}
    assert len(stub.calls) == 2


async def test_the_archived_history_replays() -> None:
    """The history in `tests/replay/histories` is this loop's; recorded again here, it replays too."""
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.client import WorkflowHistory
    from temporalio.worker import Replayer

    _, _, history = await run_loop([{"status": "synced", "interval_seconds": 300}, {"status": "gone"}])
    replayer = Replayer(workflows=ALL_WORKFLOWS)
    await replayer.replay_workflow(history)
    archived = json.loads(HISTORY.read_text(encoding="utf-8"))
    await replayer.replay_workflow(WorkflowHistory.from_json("knowledge-sync-src-1", archived))


if __name__ == "__main__":  # `python -m` from the repository root: records the archived history again
    import asyncio

    async def _record() -> None:
        _, _, history = await run_loop([{"status": "synced", "interval_seconds": 300}, {"status": "gone"}])
        HISTORY.write_text(history.to_json(), encoding="utf-8")

    asyncio.run(_record())
