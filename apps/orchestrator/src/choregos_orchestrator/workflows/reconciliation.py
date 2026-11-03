# SPDX-License-Identifier: Apache-2.0
"""`TrackerReconciliation`: the tracker's fallback polling (docs/plan/03, S3-06).

Webhooks are the nominal path. They get lost: failed delivery, unreachable platform, secret
rotated during maintenance. This workflow periodically rereads the tracker's candidates and
catches up on what is missing. It is a net, not a scheduler: it decides nothing, it hands control
back to `WorkflowInterpreter`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

from .scheduling import execute_on_its_queue

with workflow.unsafe.imports_passed_through():
    from ..activities import schedules as schedule_activities
    from ..activities import tracker as tracker_activities

RETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=5))

# Beyond this, the history grows for nothing: we restart clean with `continue_as_new`.
PASSES_BEFORE_CONTINUE = 200


@dataclass
class ReconciliationInput:
    project_slug: str
    interval_seconds: int = 60
    passes: int = 0
    max_passes: int = PASSES_BEFORE_CONTINUE


@workflow.defn(name="TrackerReconciliation", sandboxed=False)
class TrackerReconciliation:
    def __init__(self) -> None:
        self.passes = 0
        self.last: dict[str, Any] = {}
        self.fired: dict[str, Any] = {}
        self.stopped = False
        self.wake = False

    @workflow.query
    def status(self) -> dict[str, Any]:
        return {"passes": self.passes, "last": self.last, "fired": self.fired, "stopped": self.stopped}

    @workflow.signal
    def stop(self, payload: dict[str, Any] | None = None) -> None:
        """Clean stop: an archived project no longer has to poll its tracker."""
        self.stopped = True

    @workflow.signal
    def reconcile_now(self, payload: dict[str, Any] | None = None) -> None:
        """Immediate catch-up, without waiting for the interval to end."""
        self.wake = True

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        params = ReconciliationInput(**payload)
        done = 0
        while not self.stopped and done < params.max_passes:
            # A pass that fails (tracker unreachable, GitHub App not yet installed) does not kill the
            # loop: it says so in its status and runs again at the next interval. On the dev tenant,
            # on 8 October, three failures without the App had left `reconcile-dev` dead for good, and
            # the `agent-ready` issues were never read again (S22-06).
            # The project's schedules open their work items first, so that this same pass starts
            # them (S38-11). Patched: a history recorded before it replays without the call.
            if workflow.patched("fire-schedules"):
                try:
                    self.fired = await execute_on_its_queue(
                        schedule_activities.fire_schedules,
                        {"project_slug": params.project_slug},
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=RETRY,
                    )
                except ActivityError as erreur:
                    self.fired = {"error": str(erreur.cause or erreur)}
            try:
                self.last = await execute_on_its_queue(
                    tracker_activities.reconcile_tracker,
                    {"project_slug": params.project_slug},
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=RETRY,
                )
            except ActivityError as erreur:
                self.last = {"error": str(erreur.cause or erreur)}
            done += 1
            self.passes = params.passes + done
            self.wake = False
            await _wait(lambda: self.stopped or self.wake, timedelta(seconds=max(1, params.interval_seconds)))
        if self.stopped:
            return {"passes": self.passes, "stopped": True}
        workflow.continue_as_new(
            {
                "project_slug": params.project_slug,
                "interval_seconds": params.interval_seconds,
                "passes": self.passes,
                "max_passes": params.max_passes,
            }
        )
        return None


async def _wait(condition: Any, timeout: timedelta) -> bool:  # noqa: ASYNC109
    """A `wait_condition` that returns `False` on expiry rather than raising."""
    try:
        await workflow.wait_condition(condition, timeout=timeout)
    except TimeoutError:
        return False
    return True
