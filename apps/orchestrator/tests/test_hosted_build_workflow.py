"""`HostedImageBuild` (S41-08): claim, build on the executor's queue, record; a failing build is
recorded with its reason, a claim with nothing left to do builds nothing. Its history replays
(`tests/replay`)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

HISTORY = Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories" / "agent-build-S41-08.json"
PAYLOAD = {"org": "org-1", "key": "sha256:" + "b" * 64, "attempt": 1}
IMAGE = "registry.internal/builds@sha256:" + "d" * 64


async def play(claim: dict[str, Any], build: Any) -> tuple[dict[str, Any], dict[str, list[Any]], Any]:
    """The workflow against stub activities: its result, what each activity was asked, its history."""
    from choregos_orchestrator.testing import workers_repartis
    from choregos_orchestrator.workflows.agent_build import HostedImageBuild
    from temporalio.testing import WorkflowEnvironment

    asked: dict[str, list[Any]] = {"claim": [], "build": [], "record": []}

    @activity.defn(name="claim_hosted_build")
    async def claim_hosted_build(payload: dict[str, Any]) -> dict[str, Any]:
        asked["claim"].append(payload)
        return claim

    @activity.defn(name="build_hosted_image")
    async def build_hosted_image(payload: dict[str, Any]) -> dict[str, Any]:
        asked["build"].append(payload)
        if isinstance(build, Exception):
            raise build
        return dict(build)

    @activity.defn(name="record_hosted_build")
    async def record_hosted_build(payload: dict[str, Any]) -> dict[str, Any]:
        asked["record"].append(payload)
        return {"status": payload["status"], "moved": []}

    environment = await WorkflowEnvironment.start_time_skipping()
    try:
        async with workers_repartis(
            environment.client,
            activites=[claim_hosted_build, build_hosted_image, record_hosted_build],
            workflows=[HostedImageBuild],
        ):
            handle = await environment.client.start_workflow(
                "HostedImageBuild", PAYLOAD, id="hosted-build-org-1-bbbb-1", task_queue="test"
            )
            result = await handle.result()
            history = await handle.fetch_history()
    finally:
        await environment.shutdown()
    return result, asked, history


async def test_a_build_is_claimed_built_on_the_executors_queue_and_recorded_ready() -> None:
    from temporalio.api.enums.v1 import EventType

    result, asked, history = await play(
        {"status": "building"}, {"image": IMAGE, "builder": "fake", "built": True}
    )
    assert result == {"status": "ready", "moved": []}
    scheduled = {
        e.activity_task_scheduled_event_attributes.activity_type.name: (
            e.activity_task_scheduled_event_attributes.task_queue.name
        )
        for e in history.events
        if e.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
    }
    assert scheduled["build_hosted_image"] == "executor", "the queue that holds the push credential"
    assert asked["build"] == [PAYLOAD]
    assert asked["record"] == [{**PAYLOAD, "status": "ready", "image": IMAGE, "builder": "fake"}]


async def test_a_failing_build_is_recorded_failed_with_its_reason_and_not_retried() -> None:
    failure = ApplicationError(
        "pip: no matching distribution", type="hosted_build_failed", non_retryable=True
    )
    result, asked, _ = await play({"status": "building"}, failure)
    assert result == {"status": "failed", "moved": []}
    assert len(asked["build"]) == 1, "the same inputs fail the same way"
    (recorded,) = asked["record"]
    assert recorded["status"] == "failed" and "no matching distribution" in recorded["error"]


async def test_a_claim_with_nothing_left_to_do_builds_nothing() -> None:
    for claim in ({"status": "ready", "image": IMAGE}, {"status": "superseded", "attempt": 2}):
        result, asked, _ = await play(claim, {"image": IMAGE, "builder": "fake", "built": True})
        assert result == claim
        assert asked["build"] == [] and asked["record"] == []


async def test_the_archived_history_replays() -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.client import WorkflowHistory
    from temporalio.worker import Replayer

    _, _, history = await play({"status": "building"}, {"image": IMAGE, "builder": "fake", "built": True})
    replayer = Replayer(workflows=ALL_WORKFLOWS)
    await replayer.replay_workflow(history)
    archived = json.loads(HISTORY.read_text(encoding="utf-8"))
    await replayer.replay_workflow(WorkflowHistory.from_json("hosted-build-org-1-bbbb-1", archived))


if __name__ == "__main__":  # `python -m` from the repository root: records the archived history again
    import asyncio

    async def _record() -> None:
        _, _, history = await play({"status": "building"}, {"image": IMAGE, "builder": "fake", "built": True})
        HISTORY.write_text(history.to_json(), encoding="utf-8")

    asyncio.run(_record())
