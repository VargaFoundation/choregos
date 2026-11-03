# SPDX-License-Identifier: Apache-2.0
"""Which queue carries which activity (docs/plan/02 §2.1).

This module exists because **two sides** need the same table: the worker, to know what it
registers, and the workflows, to know **where to schedule**. As long as it lived in `worker.py`,
only the first side read it: a workflow does not import the worker's module, which pulls in the
Temporal client, the API and the database. So the table described a split that nothing applied:
Temporal schedules on the *workflow's* queue when the call does not name one, and the
`orchestrator` queue registers everything. The three specialised workers received nothing.

It is not only an inefficiency. At Diametral, access to the Kubernetes API is opened by a Cilium
policy aimed at `choregos/queue: executor`, the queue that, according to this table, creates the
Jobs. With `start_run` actually running on `orchestrator`, the Job creation left from a pod with
no egress right and timed out with `ConnectTimeout`: a run stuck in `queued`, a ticket in failure.
A least-privilege policy written from the declared split can only be right if that split is
**applied**.

The module depends on the standard library only: it must stay importable from a workflow's
sandbox.
"""

from __future__ import annotations

from typing import Any

#: The activities by queue: slow external calls are isolated so they do not block the decision
#: loop. An activity missing from this table stays on the queue of the workflow that calls it
#: (`orchestrator`), which carries everything: that is the default, not an oversight.
ACTIVITIES_BY_QUEUE: dict[str, tuple[str, ...]] = {
    "executor": (
        "prepare_stage",
        "start_run",
        "await_run",
        "cancel_run",
        "suspend_run",
        "release_suspended_run",
        "resume_run",
        "collect_spend",
        "record_run_outcome",
        # A decision step mints a key, calls the gateway and reads its spend, as a run's
        # preparation and its bill do (ADR 0121): the queue whose pods reach the gateway.
        "decide",
        # A build pushes to the build repository: the executor holds that credential (S41-08).
        "build_hosted_image",
        # A hosted session's pod (S41-11): started and stopped by the executor, as a run's.
        "start_session_pod",
        "stop_session_pod",
    ),
    "tracker": (
        "mirror_state",
        "update_status_comment",
        "create_human_request",
        "close_human_request",
        "notify",
        "open_pull_request",
        "enqueue_merge",
        "ensure_branch",
        "collect_run_artifacts",
        "evaluate_gates",
        "check_scope_violations",
        "triage_finding",
        "reconcile_tracker",
        "fire_schedules",
    ),
    "memory": (
        "ingest_sources",
        "ingest_alert",
        "write_run_lesson",
        "accept_pending_facts",
        "memory_ab_report",
    ),
}

#: The reverse index, built once. An activity can be on one queue only: if two entries
#: contradicted each other, the table would be ambiguous and nobody would see it.
QUEUE_BY_ACTIVITY: dict[str, str] = {}
for _queue, _names in ACTIVITIES_BY_QUEUE.items():
    for _name in _names:
        if _name in QUEUE_BY_ACTIVITY:  # pragma: no cover - the table's consistency guard
            raise RuntimeError(
                f"the activity {_name!r} is declared on two queues: "
                f"{QUEUE_BY_ACTIVITY[_name]!r} and {_queue!r}"
            )
        QUEUE_BY_ACTIVITY[_name] = _queue
del _queue, _names, _name


def activity_name(activity: Any) -> str | None:
    """The name UNDER WHICH Temporal knows an activity, or `None` if it is not one.

    It is the name declared by `@activity.defn`, not the Python function's: the two can differ,
    and the first is the one that travels in the history.
    """
    if isinstance(activity, str):
        return activity
    definition = getattr(activity, "__temporal_activity_definition", None)
    if definition is None:
        return None
    name = getattr(definition, "name", None)
    return name if isinstance(name, str) else None


def queue_of(activity: Any) -> str | None:
    """The queue that carries this activity, or `None` if the table declares none."""
    name = activity_name(activity)
    return QUEUE_BY_ACTIVITY.get(name) if name is not None else None
