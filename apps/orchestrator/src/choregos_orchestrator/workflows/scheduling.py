# SPDX-License-Identifier: Apache-2.0
"""Schedule an activity **on its own queue** (docs/plan/02 §2.1).

`workflow.execute_activity` without `task_queue` schedules on the *workflow's* queue. Every
workflow runs on `orchestrator`, so every activity ran there too, whatever queue `queues` gave it.
This wrapper reads the table and sets the `task_queue` the call leaves out: a caller that names
one keeps the last word.
"""

from __future__ import annotations

from typing import Any

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from ..queues import queue_of

#: Sentinel: `execute_activity` tells "no argument" from "the argument `None`", and an activity
#: without a parameter exists (`train_stats`, `list_fixtures`).
_ABSENT = object()


def execute_on_its_queue(activity: Any, arg: Any = _ABSENT, **options: Any) -> Any:
    """Like `workflow.execute_activity`, but on the activity's declared queue.

    Returns what Temporal returns, an awaitable, so it is used with `await`, exactly like the
    call it replaces.
    """
    if "task_queue" not in options:
        queue = queue_of(activity)
        if queue is not None:
            options["task_queue"] = queue
    if arg is _ABSENT:
        return workflow.execute_activity(activity, **options)
    return workflow.execute_activity(activity, arg, **options)


def start_on_its_queue(activity: Any, arg: Any = _ABSENT, **options: Any) -> Any:
    """Like `workflow.start_activity`, on the activity's declared queue.

    Returns the activity's handle, which can be awaited, polled (`done()`) or cancelled: what a
    workflow needs to stop waiting for an activity in favour of a signal.
    """
    if "task_queue" not in options:
        queue = queue_of(activity)
        if queue is not None:
            options["task_queue"] = queue
    if arg is _ABSENT:
        return workflow.start_activity(activity, **options)
    return workflow.start_activity(activity, arg, **options)
