# SPDX-License-Identifier: Apache-2.0
"""Test tooling: workers **spread by queue**, as the chart deploys one per queue.

This module lives in the package and not in a `conftest.py` because **three** suites need it
(`apps/orchestrator/tests`, `tests/e2e`, `tests/replay`) and a copy per suite drifts apart in
silence. It already had: the three built a single worker carrying every activity, so none could
see that the workflows scheduled everything on the same queue (defect of 2026-09-27).

**A deliberate difference from production**: here, the workflow's queue carries ONLY the
activities with no declared queue. In production, `orchestrator` carries everything, a net so that
no task is left orphaned during a version upgrade. The net would make the test blind: a badly
routed activity would run anyway. Not laying it is what turns the suite red when the routing
regresses.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Iterable, Mapping
from typing import TYPE_CHECKING, Any

from .queues import ACTIVITIES_BY_QUEUE, activity_name, queue_of

if TYPE_CHECKING:
    from temporalio.api.enums.v1 import EventType
    from temporalio.client import WorkflowHandle


def with_doubles(activities: Iterable[Any], replacements: Mapping[str, Any] | None = None) -> list[Any]:
    """The list of activities where those named in `replacements` are substituted.

    The keys are **Temporal names** (`@activity.defn(name=...)`), not function names: the declared
    name decides the queue, and it is the one that travels in the history.
    """
    replacements = dict(replacements or {})
    result: list[Any] = []
    seen: set[str] = set()
    for activity in activities:
        name = activity_name(activity)
        if name is not None and name in replacements:
            result.append(replacements[name])
            seen.add(name)
        else:
            result.append(activity)
    # A double of an activity the package does not know stays registered.
    result.extend(double for name, double in replacements.items() if name not in seen)
    return result


def group_by_queue(activities: Iterable[Any], workflow_queue: str) -> dict[str, list[Any]]:
    """Group activities by queue. Those with no declared queue go on the workflow's queue."""
    by_queue: dict[str, list[Any]] = {workflow_queue: []}
    for queue in ACTIVITIES_BY_QUEUE:
        by_queue.setdefault(queue, [])
    for activity in activities:
        by_queue[queue_of(activity) or workflow_queue].append(activity)
    return by_queue


@contextlib.asynccontextmanager
async def workers_by_queue(
    client: Any,
    *,
    activities: Iterable[Any],
    workflows: Iterable[Any],
    workflow_queue: str = "test",
    replacements: Mapping[str, Any] | None = None,
    **options: Any,
) -> AsyncIterator[dict[str, list[Any]]]:
    """One worker per queue, for the length of the block. Yields the split, so a test can assert it."""
    from temporalio.worker import Worker

    by_queue = group_by_queue(with_doubles(activities, replacements), workflow_queue)
    carried = list(workflows)
    # A queue without activities has no worker: Temporal refuses an empty worker, and a test that
    # supplies only a subset of the activities (the doubles of `tests/replay`) leaves some queues
    # empty. What no worker carries, no workflow schedules either.
    workers = [
        Worker(
            client,
            task_queue=queue,
            workflows=carried if queue == workflow_queue else [],
            activities=queue_activities,
            **options,
        )
        for queue, queue_activities in by_queue.items()
        if queue_activities or (queue == workflow_queue and carried)
    ]
    async with contextlib.AsyncExitStack() as stack:
        for worker in workers:
            await stack.enter_async_context(worker)
        yield by_queue


#: The events that close a run: once one is in the history, no other event will follow.
_CLOSED = (
    "EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED",
    "EVENT_TYPE_WORKFLOW_EXECUTION_FAILED",
    "EVENT_TYPE_WORKFLOW_EXECUTION_TIMED_OUT",
    "EVENT_TYPE_WORKFLOW_EXECUTION_CANCELED",
    "EVENT_TYPE_WORKFLOW_EXECUTION_TERMINATED",
    "EVENT_TYPE_WORKFLOW_EXECUTION_CONTINUED_AS_NEW",
)


async def wait_for_event(
    handle: WorkflowHandle[Any, Any],
    event_type: EventType.ValueType,
    *,
    count: int = 1,
    seconds: float = 30.0,
) -> None:
    """Return once the workflow's history holds `count` events of `event_type`.

    A signal sent straight after `start_workflow` can reach the server before the workflow's first
    task has run: it then lands in the first activation, before `run` starts, and a loop that a
    `stop` signal ends never runs a cycle. A test that signals a running loop first waits here for
    the event that proves the loop is running (the timer it waits on, the activity it scheduled).
    Polling the history does not unlock the time-skipping test server's clock: only awaiting a
    result does, so a timer the loop waits on does not fire while the test waits.

    Fails with an `AssertionError` after `seconds`, or as soon as the run is closed.
    """
    import asyncio

    from temporalio.api.enums.v1 import EventType

    wanted = EventType.Name(event_type)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while True:
        seen = [EventType.Name(event.event_type) for event in (await handle.fetch_history()).events]
        if seen.count(wanted) >= count:
            return
        closed = bool(seen) and seen[-1] in _CLOSED
        if closed or loop.time() >= deadline:
            why = "the run closed" if closed else f"{seconds} s"
            raise AssertionError(
                f"{count} x {wanted} expected in {handle.id}'s history, "
                f"{seen.count(wanted)} after {why}; seen: {seen}"
            )
        await asyncio.sleep(0.05)
