"""`TrackerReconciliation` : le polling de secours du tracker (docs/plan/03, S3-06).

Les webhooks sont le chemin nominal. Ils se perdent : livraison en échec, plateforme
injoignable, secret tourné pendant une maintenance. Ce workflow relit périodiquement les
candidats du tracker et rattrape ce qui manque — c'est un filet, pas un ordonnanceur :
il ne décide rien, il redonne la main à `WorkflowInterpreter`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from ..activities import tracker as tracker_activities

RETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=5))

# Au-delà, l'historique s'allonge pour rien : on repart propre avec `continue_as_new`.
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
        self.stopped = False
        self.wake = False

    @workflow.query
    def status(self) -> dict[str, Any]:
        return {"passes": self.passes, "last": self.last, "stopped": self.stopped}

    @workflow.signal
    def stop(self, payload: dict[str, Any] | None = None) -> None:
        """Arrêt propre : un projet archivé n'a plus à interroger son tracker."""
        self.stopped = True

    @workflow.signal
    def reconcile_now(self, payload: dict[str, Any] | None = None) -> None:
        """Rattrapage immédiat, sans attendre la fin de l'intervalle."""
        self.wake = True

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        params = ReconciliationInput(**payload)
        done = 0
        while not self.stopped and done < params.max_passes:
            self.last = await workflow.execute_activity(
                tracker_activities.reconcile_tracker,
                {"project_slug": params.project_slug},
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=RETRY,
            )
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
    """`wait_condition` qui rend `False` sur expiration plutôt que de lever."""
    try:
        await workflow.wait_condition(condition, timeout=timeout)
    except TimeoutError:
        return False
    return True
