# SPDX-License-Identifier: Apache-2.0
"""`ActionWorkflow` (ADR 0035): an approved action, run effect by effect, never in the request
that approves it.

Each effect is an activity, under its key (`<action>:<n>`); a transient failure is retried, a
final refusal (`EffetRefuse`, a recorded error type) stops the rest. What had been done is then
COMPENSATED, in reverse, and the action says what it could not undo. `action-<id>`: one workflow
per action.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

from ..wire_names import both_names, read_key, require_key
from .activity_names import named
from .scheduling import execute_on_its_queue

with workflow.unsafe.imports_passed_through():
    from ..activities import actions as action_activities

#: An effect can ask to wait for the EVIDENCE that it served (S20-08): it returns
#: `{"attendre_une_preuve": {"jusqu_a": <iso>}}` (recorded payload keys), and the workflow waits
#: for the `preuve` signal (recorded wire name) until then. Histories from before this marker had
#: no effect that asked for it.
AWAITED_EVIDENCE_PATCH = "preuve-attendue"  # lang: fr-ok — a patch id is never renamed


class MissingEvidence(Exception):  # noqa: N818 - a named refusal: the evidence did not come, or says no
    pass


READ = RetryPolicy(maximum_attempts=5, initial_interval=timedelta(seconds=2))
#: A transient effect (429, 503, network) is retried; a refusal never is.
EFFECT = RetryPolicy(
    maximum_attempts=6,
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=5),
    # Both spellings (S45-03): the activities raise the French types until S45-39, and a history
    # recorded before it keeps them.
    non_retryable_error_types=both_names("EffectRefused", "ActionNotFound"),
)


@workflow.defn(name="ActionWorkflow", sandboxed=False)
class ActionWorkflow:
    def __init__(self) -> None:
        self.done: list[int] = []
        self.state = "running"
        #: The evidence received, by position of the effect that waited for it.
        self.evidence_by_position: dict[int, dict[str, Any]] = {}

    @workflow.query
    def status(self) -> dict[str, Any]:
        return {"state": self.state, "done": list(self.done)}

    @workflow.signal
    def preuve(self, payload: dict[str, Any]) -> None:  # lang: fr-ok signal wire name, recorded in histories
        """What the outside world observed (a collector report, a delivery): `ok` and `detail`."""
        self.evidence_by_position[int(payload.get("position", -1))] = dict(payload)

    @workflow.signal(name="evidence")
    def evidence(self, payload: dict[str, Any]) -> None:
        """The English name of `preuve` (S34-02): senders use it; `preuve` stays accepted for the
        signals already in histories and the senders of an older version."""
        self.preuve(payload)

    @workflow.run
    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        action_id = str(params["action_id"])
        plan = await execute_on_its_queue(
            named(action_activities.load_action, action_activities.charger_l_action),
            action_id,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=READ,
        )
        try:
            for position in range(int(require_key(plan, "effects"))):
                executed = await execute_on_its_queue(
                    named(action_activities.execute_effect, action_activities.executer_l_effet),
                    {"action_id": action_id, "position": position},
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=EFFECT,
                )
                self.done.append(position)
                result = executed.get("result") if isinstance(executed, dict) else None
                awaited = read_key(result, "await_evidence") if isinstance(result, dict) else None
                if awaited and workflow.patched(AWAITED_EVIDENCE_PATCH):
                    await self._wait_for_evidence(action_id, position, dict(awaited))
        except (ActivityError, MissingEvidence) as error:
            if isinstance(error, ActivityError):
                cause = str(error.cause) if error.cause is not None else str(error)
            else:
                cause = str(error)
            self.state = "compensating"
            compensations = []
            for position in reversed(self.done):
                compensations.append(
                    await execute_on_its_queue(
                        named(action_activities.compensate_effect, action_activities.compenser_l_effet),
                        {"action_id": action_id, "position": position},
                        start_to_close_timeout=timedelta(minutes=5),
                        retry_policy=READ,
                    )
                )
            self.state = "failed"
            await execute_on_its_queue(
                named(action_activities.close_action, action_activities.cloturer_l_action),
                {"action_id": action_id, "status": "failed", "error": cause, "compensations": compensations},
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=READ,
            )
            return {"status": "failed", "error": cause, "compensations": compensations}
        self.state = "succeeded"
        await execute_on_its_queue(
            named(action_activities.close_action, action_activities.cloturer_l_action),
            {"action_id": action_id, "status": "succeeded"},
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=READ,
        )
        return {"status": "succeeded", "effects": list(self.done)}

    async def _wait_for_evidence(self, action_id: str, position: int, awaited: dict[str, Any]) -> None:
        """The effect is done; the action waits for the evidence that it served, until its deadline.
        If it comes and is favourable, the rest resumes; if it is contrary or missing, the action
        fails and what was done is compensated, as for a refusal."""
        until = datetime.fromisoformat(str(require_key(awaited, "until")))
        self.state = "awaiting_evidence"
        await execute_on_its_queue(
            named(action_activities.mark_awaiting_evidence, action_activities.marquer_l_attente),
            {
                "action_id": action_id,
                "position": position,
                # Recorded payload keys (ADR 0098 §4): never renamed.
                "en_attente": True,
                "jusqu_a": until.isoformat(),
            },
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=READ,
        )
        delay = max(until - workflow.now(), timedelta(0))
        with contextlib.suppress(TimeoutError):
            await workflow.wait_condition(lambda: position in self.evidence_by_position, timeout=delay)
        evidence = self.evidence_by_position.get(position)
        if evidence is None:
            raise MissingEvidence(f"no evidence before {until.isoformat()}: timed out")
        if not evidence.get("ok"):
            raise MissingEvidence(f"the evidence says no: {evidence.get('detail') or 'no detail'}")
        self.state = "running"
        await execute_on_its_queue(
            named(action_activities.mark_awaiting_evidence, action_activities.marquer_l_attente),
            # Recorded payload keys (ADR 0098 §4): never renamed.
            {"action_id": action_id, "position": position, "en_attente": False, "preuve": evidence},
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=READ,
        )
