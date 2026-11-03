"""Is the split of activities by queue **applied**, or only declared?

On 2026-09-27, on Diametral's dev tenant, a run stayed `queued` and the ticket died on a
`ConnectTimeout` to the Kubernetes API. The platform's Cilium policy opens the API only to pods
carrying `choregos/queue: executor`, the queue that, according to `queues`, creates the
Jobs. But `workflow.execute_activity` without `task_queue` schedules on the *workflow*'s queue:
`start_run` therefore ran on `orchestrator`, in a pod with no right to go out.

The table had existed since the first day and nobody could see it: the three Temporal suites
mounted a single worker carrying **all** the activities, which answers whatever queue is
asked. These tests query the history, the only place that says on which queue an activity was
really scheduled.
"""

from __future__ import annotations

from typing import Any

import pytest

from .conftest import Fixture, stage_result
from .test_interpreter import scripted, start


async def _scheduled_queues(handle: Any) -> tuple[dict[str, str], bool]:
    """The queues by activity, and whether the workflow is already dead.

    The second member avoids polling for a minute a workflow that has finished failing: an activity
    scheduled on a queue nobody listens to kills the ticket within seconds.
    """
    history = await handle.fetch_history()
    files: dict[str, str] = {}
    dead = False
    for event in history.events:
        attributes = event.activity_task_scheduled_event_attributes
        if attributes.activity_type.name:
            files[attributes.activity_type.name] = attributes.task_queue.name
        if event.HasField("workflow_execution_failed_event_attributes"):
            dead = True
    return files, dead


@pytest.mark.asyncio
async def test_the_history_says_on_which_queue_each_activity_is_scheduled(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """`start_run` on `executor`, `mirror_state` on `tracker`, `load_context` on the workflow queue.

    This is the assertion the Diametral network policy depends on. Without the routing, the three
    values are `test`, the workflow queue.

    The history carries the queue from the **scheduling**, before a worker has taken the task:
    the claim therefore does not depend on a worker listening, and a broken routing shows at
    once instead of timing out after a minute.
    """
    scripted(setup.adapters, stage_result("spec written", outputs={"size": "M", "risk": "low"}))

    async with worker_factory():
        handle = await start(temporal_env, setup)

        files = await _wait_for_scheduling(handle, "prepare_stage")
        assert files["prepare_stage"] == "executor", (
            "the step preparation must leave from `executor`: it is the only queue to which "
            f"the Cilium policy opens the Kubernetes API (seen: {files['prepare_stage']!r})"
        )
        assert files.get("mirror_state") == "tracker", (
            f"tracker calls are isolated and rate-limited (seen: {files.get('mirror_state')!r})"
        )
        assert files.get("load_context") == "test", (
            "an activity with no declared queue stays on the workflow queue, which carries everything "
            f"(seen: {files.get('load_context')!r})"
        )

        # `start_run`, the one that creates the Kubernetes Job, is scheduled only once the step is
        # prepared: it is the one that timed out in `ConnectTimeout` on the tenant.
        files = await _wait_for_scheduling(handle, "start_run")
        assert files["start_run"] == "executor", f"seen: {files['start_run']!r}"


async def _wait_for_scheduling(handle: Any, activity: str, *, timeout: int = 60) -> dict[str, str]:
    """Waits for an activity to appear in the history, and returns the queues seen."""
    import asyncio

    files: dict[str, str] = {}
    for _ in range(timeout * 10):
        files, dead = await _scheduled_queues(handle)
        if activity in files:
            return files
        if dead:
            raise AssertionError(f"the workflow died before scheduling {activity}; queues seen: {files}")
        await asyncio.sleep(0.1)
    raise AssertionError(f"{activity} never scheduled (queues seen: {files})")


def test_the_table_only_names_activities_that_exist() -> None:
    """An activity rename that forgets the table would make it mute, without breaking anything.

    This is the most discreet failure mode of this mechanism: the activity stops being routed, it
    runs on the workflow queue, everything works, until the day a network policy counts on the
    declared queue.
    """
    from choregos_orchestrator.activities import ALL_ACTIVITIES
    from choregos_orchestrator.queues import QUEUE_BY_ACTIVITY, activity_name
    from choregos_orchestrator.workflows import WORKFLOW_ACTIVITIES

    known = {
        name
        for activity in [*ALL_ACTIVITIES, *WORKFLOW_ACTIVITIES]
        if (name := activity_name(activity)) is not None
    }
    ghosts = sorted(set(QUEUE_BY_ACTIVITY) - known)
    assert not ghosts, f"the table routes activities that no longer exist: {ghosts}"


def test_a_call_that_specifies_its_queue_keeps_the_last_word() -> None:
    """The envelope sets a default; it does not confiscate the caller's decision."""
    from choregos_orchestrator.activities import stage as stage_activities
    from choregos_orchestrator.activities.interpretation import load_context
    from choregos_orchestrator.workflows import scheduling

    seen: list[dict[str, Any]] = []

    def fake_execute_activity(activity: Any, *args: Any, **options: Any) -> None:
        seen.append(options)

    original = scheduling.workflow.execute_activity
    scheduling.workflow.execute_activity = fake_execute_activity  # type: ignore[assignment]
    try:
        scheduling.execute_on_its_queue(stage_activities.start_run, {})
        scheduling.execute_on_its_queue(stage_activities.start_run, {}, task_queue="mine")
        scheduling.execute_on_its_queue(load_context, {})
    finally:
        scheduling.workflow.execute_activity = original  # type: ignore[assignment]

    assert seen[0]["task_queue"] == "executor"
    assert seen[1]["task_queue"] == "mine"
    assert "task_queue" not in seen[2], "an activity with no declared queue must not receive one"
