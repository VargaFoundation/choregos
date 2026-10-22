"""`AgentSessionPod` (S41-11): one pod per session, started on the executor's queue, kept alive by
each response's `touch`, stopped at the first of a `close`, the idle wait, the session's expiry; a
pod that cannot start closes the session `pod_failed`. Its history replays (`tests/replay`)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

HISTORY = Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories" / "agent-session-S41-11.json"
WORKFLOW_ID = "agent-session-s1"
POD = {"state": "running", "kind": "fake", "name": "fake-session-s1", "namespace": "choregos-runs-demo"}


async def play(
    *,
    idle_s: float = 600,
    expires_in: timedelta = timedelta(hours=1),
    start: Any = None,
    script: Any = None,
) -> tuple[dict[str, Any], dict[str, list[Any]], Any]:
    """The workflow against stub activities. `script(handle, env)` acts on it while it runs."""
    from choregos_orchestrator.testing import workers_repartis
    from choregos_orchestrator.workflows.agent_session import AgentSessionPod
    from temporalio.testing import WorkflowEnvironment

    asked: dict[str, list[Any]] = {"start": [], "stop": []}

    @activity.defn(name="start_session_pod")
    async def start_session_pod(payload: dict[str, Any]) -> dict[str, Any]:
        asked["start"].append(payload)
        if isinstance(start, Exception):
            raise start
        return POD

    @activity.defn(name="stop_session_pod")
    async def stop_session_pod(payload: dict[str, Any]) -> dict[str, Any]:
        asked["stop"].append(payload)
        return {"state": "stopped", "failed_turns": 0}

    environment = await WorkflowEnvironment.start_time_skipping()
    try:
        async with workers_repartis(
            environment.client,
            activites=[start_session_pod, stop_session_pod],
            workflows=[AgentSessionPod],
        ):
            now = await environment.get_current_time()
            handle = await environment.client.start_workflow(
                "AgentSessionPod",
                {
                    "session_id": "s1",
                    "org": "acme",
                    "idle_s": idle_s,
                    "expires_at": (now + expires_in).isoformat(),
                },
                id=WORKFLOW_ID,
                task_queue="test",
            )
            if script is not None:
                await script(handle, environment)
            result = await handle.result()
            history = await handle.fetch_history()
    finally:
        await environment.shutdown()
    return result, asked, history


async def test_the_pod_starts_once_on_the_executors_queue_and_stops_when_idle() -> None:
    from temporalio.api.enums.v1 import EventType

    result, asked, history = await play(idle_s=60)
    assert asked["start"] == [{"session_id": "s1"}]
    assert asked["stop"] == [{"session_id": "s1", "reason": "idle"}]
    assert result["reason"] == "idle"
    queues = {
        e.activity_task_scheduled_event_attributes.activity_type.name: (
            e.activity_task_scheduled_event_attributes.task_queue.name
        )
        for e in history.events
        if e.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
    }
    assert queues == {"start_session_pod": "executor", "stop_session_pod": "executor"}


async def test_each_touch_restarts_the_idle_wait() -> None:
    seen: dict[str, Any] = {}

    async def touch_three_times(handle: Any, environment: Any) -> None:
        for _ in range(3):
            await environment.sleep(40)  # each under the 60 s idle wait, 120 s in all
            await handle.signal("touch", {})
        description = await handle.describe()
        seen["running"] = description.status.name

    result, asked, _ = await play(idle_s=60, script=touch_three_times)
    assert seen["running"] == "RUNNING", "touched within the idle wait, the pod stays"
    assert len(asked["start"]) == 1, "a touch starts nothing"
    assert result["reason"] == "idle"


async def test_close_stops_the_pod_with_its_reason() -> None:
    async def close(handle: Any, _: Any) -> None:
        await handle.signal("close", {"reason": "budget_exceeded"})

    result, asked, _ = await play(script=close)
    assert asked["stop"] == [{"session_id": "s1", "reason": "budget_exceeded"}]
    assert result["reason"] == "budget_exceeded"


async def test_the_session_expiry_stops_the_pod_before_the_idle_wait() -> None:
    from temporalio.api.enums.v1 import EventType

    result, asked, history = await play(idle_s=600, expires_in=timedelta(seconds=90))
    assert asked["stop"] == [{"session_id": "s1", "reason": "expired"}]
    assert result["reason"] == "expired"
    started = history.events[0].event_time.ToDatetime()
    (stop,) = [
        e.event_time.ToDatetime()
        for e in history.events
        if e.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
        and e.activity_task_scheduled_event_attributes.activity_type.name == "stop_session_pod"
    ]
    assert (stop - started).total_seconds() < 120, "stopped at the expiry, not after the idle wait"


async def test_a_pod_that_cannot_start_closes_the_session_pod_failed() -> None:
    # Not marked non-retryable by the activity: the workflow's retry policy alone stops the retries.
    refusal = ApplicationError("image pull refused", type="session_pod_refused")
    result, asked, _ = await play(start=refusal)
    assert len(asked["start"]) == 1, "a refusal is not retried"
    assert asked["stop"] == [{"session_id": "s1", "reason": "pod_failed"}]
    assert result["reason"] == "pod_failed"


async def _closed(handle: Any, _: Any) -> None:
    await handle.signal("touch", {})
    await handle.signal("close", {"reason": "closed"})


async def test_the_archived_history_replays() -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.client import WorkflowHistory
    from temporalio.worker import Replayer

    _, _, history = await play(script=_closed)
    replayer = Replayer(workflows=ALL_WORKFLOWS)
    await replayer.replay_workflow(history)
    archived = json.loads(HISTORY.read_text(encoding="utf-8"))
    await replayer.replay_workflow(WorkflowHistory.from_json(WORKFLOW_ID, archived))


if __name__ == "__main__":  # `python -m` from the repository root: records the archived history again
    import asyncio

    async def _record() -> None:
        _, _, history = await play(script=_closed)
        HISTORY.write_text(history.to_json(), encoding="utf-8")

    asyncio.run(_record())
