# SPDX-License-Identifier: Apache-2.0
"""The guarantees of a transition, and the events they wait for.

Methods moved as they were from `workflows/interpreter.py`; the handlers stay on the class."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow

from ..scheduling import execute_on_its_queue

with workflow.unsafe.imports_passed_through():
    from ...activities import gates as gate_activities

from ..interpreter import GATES_REREAD_AFTER_WAIT
from .base import InterpreterBase
from .helpers import DEFAULT_RETRY, InterpreterInput, wait_signal


class GatesMixin(InterpreterBase):
    """The guarantees of a transition, and the events they wait for."""

    async def _gates(self, params: InterpreterInput, transition: Any, run_id: str) -> list[Any]:
        if not transition.gates:
            return []
        outcomes = await self._evaluate_all_gates(params, transition, run_id)
        pending = [o for o in outcomes if o.pending]
        if pending and transition.timeout_hours:
            resolved = await self._await_async_gates(params, transition, run_id, [o.name for o in pending])
            # Replaced by name: the wait returns the pending guarantees only, or all of them once
            # they are read again (`GATES_REREAD_AFTER_WAIT`).
            fresh = {o.name for o in resolved}
            outcomes = [o for o in outcomes if o.name not in fresh] + resolved
        return outcomes

    async def _evaluate_all_gates(self, params: InterpreterInput, transition: Any, run_id: str) -> list[Any]:
        """Every guarantee of the transition, read now — on entry, and again once the pending ones
        have their verdict: a synchronous one may have changed meanwhile (#359)."""
        from choregos_core import GateOutcome

        raw = await execute_on_its_queue(
            gate_activities.evaluate_gates,
            {
                "project_id": params.project_id,
                "work_item_id": params.work_item_id,
                "run_id": run_id,
                # What the transition declares it produces: the generic `outputs_present`
                # guarantee uses it when it has no explicit list.
                "expected_outputs": list(transition.outputs),
                "gates": [{"name": g.name, "params": g.params} for g in transition.gates],
            },
            start_to_close_timeout=timedelta(minutes=3),
            retry_policy=DEFAULT_RETRY,
        )
        return [
            GateOutcome(
                name=item["name"],
                passed=item["passed"],
                pending=item["pending"],
                detail=item["detail"],
                annotations=list(item.get("annotations", [])),
            )
            for item in raw
        ]

    async def _await_async_gates(
        self, params: InterpreterInput, transition: Any, run_id: str, names: list[str]
    ) -> list[Any]:
        """Wait for the external events (CI, review) then re-evaluate, until the timeout."""
        from choregos_core import GateOutcome

        deadline = timedelta(hours=transition.timeout_hours or 24)
        waited = timedelta()
        step = timedelta(minutes=5)
        while waited < deadline:
            received = await wait_signal(lambda: bool(self.inbox) or self.stopped, step)
            while self.inbox:
                event = self.inbox.popleft()
                self._absorb(event)
            raw = await execute_on_its_queue(
                gate_activities.evaluate_gates,
                {
                    "project_id": params.project_id,
                    "work_item_id": params.work_item_id,
                    "run_id": run_id,
                    "expected_outputs": list(transition.outputs),
                    "gates": [
                        {"name": g.name, "params": g.params} for g in transition.gates if g.name in names
                    ],
                },
                start_to_close_timeout=timedelta(minutes=3),
                retry_policy=DEFAULT_RETRY,
            )
            outcomes = [
                GateOutcome(
                    name=item["name"], passed=item["passed"], pending=item["pending"], detail=item["detail"]
                )
                for item in raw
            ]
            if not any(o.pending for o in outcomes) or self.stopped:
                # The condition first, `patched` next: an old history replayed here answers no
                # without the marker being asked any earlier (S22-32).
                if not self.stopped and workflow.patched(GATES_REREAD_AFTER_WAIT):
                    complete = await self._evaluate_all_gates(params, transition, run_id)
                    again = [o.name for o in complete if o.pending]
                    if not again:
                        return complete
                    # A guarantee pending again (CI re-run on a new push): wait for it as before.
                    names = again
                else:
                    return outcomes
            waited += step
            if received is False:  # wait_condition expired with no signal
                continue
        return [GateOutcome(name=name, passed=False, detail="timed out") for name in names]

    def _absorb(self, event: dict[str, Any]) -> None:
        """Translate an incoming event into `last_outcome` for the next selection."""
        kind = event.get("type", "")
        if (
            kind in {"ci.run.failed", "scm.check.completed"}
            and event.get("payload", {}).get("conclusion") == "failure"
        ):
            self.last_outcome = "ci_failed"
        elif (
            kind == "scm.pr.review_submitted" and event.get("payload", {}).get("state") == "changes_requested"
        ):
            self.last_outcome = "changes_requested"
        elif kind == "scm.pr.merged":
            self.last_outcome = "merged"
