"""`KnowledgeDistill` (S42-10c): plan, one activity per part, reduce, publish; the budget is checked
before each part; a refusal or a failing part ends in `fail_distillation` with nothing published. Its
history replays (`tests/replay`)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

HISTORY = (
    Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories" / "knowledge-distill-S42-10.json"
)
PAYLOAD = {
    "org": "varga",
    "base_id": "kb-1",
    "requested_by": "admin@varga.dev",
    "exclude": ["*.xlsx"],
    "max_usd": 5,
}


def plan_of(costs: list[float | None], budget: float = 5.0) -> dict[str, Any]:
    return {
        "status": "planned",
        "model": "m",
        "parts": [
            {"index": n, "digest": f"sha256:{n}", "characters": 10, "est_usd": c} for n, c in enumerate(costs)
        ],
        "merge_est_usd": 0.1,
        "budget_usd": budget,
    }


def stubs(
    planned: dict[str, Any], *, part_cost: float = 1.0, failing: int | None = None, refuse: bool = False
) -> Any:
    calls: list[tuple[str, dict[str, Any]]] = []

    @activity.defn(name="plan_distillation")
    async def plan_distillation(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(("plan", payload))
        if refuse:
            raise ApplicationError(
                "too many parts", "too_large", type="DistillationError", non_retryable=True
            )
        return planned

    @activity.defn(name="distill_part")
    async def distill_part(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(("part", payload))
        if payload["index"] == failing:
            raise ApplicationError(
                "the gateway answered 503", "gateway", type="DistillationError", non_retryable=True
            )
        return {"status": "done", "cost_usd": part_cost}

    @activity.defn(name="reduce_distillation")
    async def reduce_distillation(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(("reduce", payload))
        return {"status": "done", "digest": "sha256:merged", "cost_usd": 0.1}

    @activity.defn(name="publish_distillation")
    async def publish_distillation(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(("publish", payload))
        return {"status": "pending_review", "version": 1}

    @activity.defn(name="fail_distillation")
    async def fail_distillation(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(("fail", payload))
        return {"status": "failed", "code": payload["code"]}

    found = [plan_distillation, distill_part, reduce_distillation, publish_distillation, fail_distillation]
    return found, calls


async def run(
    planned: dict[str, Any], **options: Any
) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any]]], Any]:
    from choregos_orchestrator.workflows.knowledge_distill import KnowledgeDistill
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    activities, calls = stubs(planned, **options)
    environment = await WorkflowEnvironment.start_time_skipping()
    try:
        async with Worker(
            environment.client, task_queue="test", workflows=[KnowledgeDistill], activities=activities
        ):
            handle = await environment.client.start_workflow(
                "KnowledgeDistill",
                PAYLOAD,
                id="knowledge-distill-kb-1",
                task_queue="test",
                execution_timeout=timedelta(days=1),
            )
            result = await handle.result()
            history = await handle.fetch_history()
    finally:
        await environment.shutdown()
    return result, calls, history


async def test_the_steps_run_in_order_and_publish_once() -> None:
    result, calls, _ = await run(plan_of([1.0, 1.0, 1.0]))
    assert [name for name, _ in calls] == ["plan", "part", "part", "part", "reduce", "publish"]
    assert result == {"status": "pending_review", "version": 1}
    assert calls[0][1] == {**{k: PAYLOAD[k] for k in ("org", "base_id", "exclude")}, "max_usd": 5}
    assert [c[1]["spent_usd"] for c in calls if c[0] == "part"] == [0.0, 1.0, 2.0]
    assert calls[4][1]["digests"] == ["sha256:0", "sha256:1", "sha256:2"]
    assert calls[5][1]["digest"] == "sha256:merged" and calls[5][1]["parts"] == 3
    assert calls[5][1]["spent_usd"] == 3.1


async def test_a_failing_part_fails_the_run_and_publishes_nothing() -> None:
    result, calls, _ = await run(plan_of([1.0, 1.0, 1.0]), failing=1)
    assert [name for name, _ in calls] == ["plan", "part", "part", "fail"]
    assert result == {"status": "failed", "code": "gateway"}
    failure = calls[-1][1]
    assert failure["error"] == "the gateway answered 503" and failure["spent_usd"] == 1.0
    assert "publish" not in {name for name, _ in calls}


async def test_a_refused_plan_is_recorded_with_its_code() -> None:
    result, calls, _ = await run(plan_of([]), refuse=True)
    assert [name for name, _ in calls] == ["plan", "fail"]
    assert result["code"] == "too_large" and calls[1][1]["error"] == "too many parts"


async def test_the_budget_is_checked_before_each_call_not_after_the_run() -> None:
    result, calls, _ = await run(plan_of([1.0] * 4, budget=3.5), part_cost=2.0)
    assert [name for name, _ in calls] == ["plan", "part", "part", "fail"]
    assert result["code"] == "over_budget"
    assert "nothing was published" in calls[-1][1]["error"] and calls[-1][1]["spent_usd"] == 4.0


async def test_an_unpriced_part_never_trips_the_budget() -> None:
    result, calls, _ = await run(plan_of([None, None], budget=0.01), part_cost=0.0)
    assert result["status"] == "pending_review" and [n for n, _ in calls][-1] == "publish"


async def test_a_base_that_left_ends_quietly() -> None:
    result, calls, _ = await run({"status": "gone"})
    assert result == {"status": "gone"} and [n for n, _ in calls] == ["plan"]


async def test_a_cancelled_run_is_completed_as_cancelled_and_publishes_nothing() -> None:
    import asyncio

    from choregos_orchestrator.workflows.knowledge_distill import KnowledgeDistill
    from temporalio.client import WorkflowFailureError
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    activities, calls = stubs(plan_of([1.0, 1.0, 1.0]))

    @activity.defn(name="distill_part")
    async def slow_part(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(("part", payload))
        await asyncio.sleep(3600)
        return {"status": "done", "cost_usd": 1.0}

    activities = [a for a in activities if a.__temporal_activity_definition.name != "distill_part"]  # type: ignore[attr-defined]
    activities.append(slow_part)
    environment = await WorkflowEnvironment.start_time_skipping()
    try:
        async with Worker(
            environment.client, task_queue="test", workflows=[KnowledgeDistill], activities=activities
        ):
            handle = await environment.client.start_workflow(
                "KnowledgeDistill",
                PAYLOAD,
                id="knowledge-distill-kb-1",
                task_queue="test",
                execution_timeout=timedelta(days=1),
            )
            while not any(name == "part" for name, _ in calls):
                await asyncio.sleep(0.05)
            await handle.cancel()
            try:
                result = await handle.result()
            except WorkflowFailureError:  # the workflow must answer, not end cancelled
                result = {"status": "workflow-cancelled"}
    finally:
        await environment.shutdown()
    names = [name for name, _ in calls]
    assert names[-1] == "fail" and "publish" not in names and "reduce" not in names
    assert calls[-1][1]["code"] == "cancelled"
    assert result == {"status": "failed", "code": "cancelled"}


async def test_the_archived_history_replays() -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.client import WorkflowHistory
    from temporalio.worker import Replayer

    _, _, history = await run(plan_of([1.0, 1.0]))
    replayer = Replayer(workflows=ALL_WORKFLOWS)
    await replayer.replay_workflow(history)
    archived = json.loads(HISTORY.read_text(encoding="utf-8"))
    await replayer.replay_workflow(WorkflowHistory.from_json("knowledge-distill-kb-1", archived))


if __name__ == "__main__":  # `python -m` from the repository root: records the archived history again
    import asyncio

    async def _record() -> None:
        _, _, history = await run(plan_of([1.0, 1.0]))
        HISTORY.write_text(history.to_json(), encoding="utf-8")

    asyncio.run(_record())
