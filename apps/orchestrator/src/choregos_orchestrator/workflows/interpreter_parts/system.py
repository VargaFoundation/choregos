# SPDX-License-Identifier: Apache-2.0
"""A system transition: guarantees, governed action, fan-out, dates.

Methods moved as they were from `workflows/interpreter.py`; the handlers stay on the class."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.exceptions import ActivityError

from ...wire_names import read_key
from ..activity_names import named
from ..scheduling import execute_on_its_queue

with workflow.unsafe.imports_passed_through():
    from choregos_contracts.workflow import effet_de_la_transition
    from choregos_core import WorkflowEngine
    from choregos_core.dsl.dates import DateIllisible, echeance
    from choregos_core.gates import ACTION_REGLEE, GateOutcome

    from ...activities import actions as action_activities
    from ...activities import children as children_activities
    from ...activities import scm as scm_activities

from ..interpreter import FAN_OUT, TRANSITION_ACTIONS
from .base import InterpreterBase
from .helpers import ACTION_REREAD, CHILDREN_REREAD, DEFAULT_RETRY, InterpreterInput, wait_signal


class SystemMixin(InterpreterBase):
    """A system transition: guarantees, governed action, fan-out, dates."""

    async def _run_system(self, params: InterpreterInput, engine: WorkflowEngine, transition: Any) -> Any:
        key = transition.key
        attempt = self.attempts[key]
        self.attempts[key] = attempt + 1

        if transition.spawn is not None and workflow.patched(FAN_OUT):
            return await self._run_spawn(params, engine, transition)
        if transition.action is not None and workflow.patched(TRANSITION_ACTIONS):
            return await self._run_action(params, engine, transition, attempt + 1)

        # What the platform does is WRITTEN (`does`, #175); a workflow published before carried it in
        # the target state's name, and is still read that way: a recorded history replays identically.
        effect = effet_de_la_transition(transition)
        if effect == "open_pr":
            try:
                # `draft` only when the transition asks for one (S51-11): every other transition
                # schedules the same input as before, so a recorded history replays unchanged.
                draft = transition.pull_request is not None and transition.pull_request.draft
                await execute_on_its_queue(
                    scm_activities.open_pull_request,
                    {
                        "project_id": params.project_id,
                        "work_item_id": params.work_item_id,
                        **({"draft": True} if draft else {}),
                    },
                    start_to_close_timeout=timedelta(minutes=3),
                    retry_policy=DEFAULT_RETRY,
                )
            except ActivityError as error:
                escalated = await self._no_branch_refused(params, engine, transition, error)
                if escalated is None:
                    raise
                return escalated
        outcomes = await self._gates(params, transition, self.current_run or self.last_run or "")
        if effect == "merge_pr" and all(not o.blocking and not o.pending for o in outcomes):
            await execute_on_its_queue(
                scm_activities.enqueue_merge,
                {"project_id": params.project_id, "work_item_id": params.work_item_id},
                start_to_close_timeout=timedelta(minutes=3),
                retry_policy=DEFAULT_RETRY,
            )
        return engine.after_gates(transition, outcomes, self._counted_attempts(key))

    async def _run_action(
        self, params: InterpreterInput, engine: WorkflowEngine, transition: Any, attempt: int
    ) -> Any:
        """A transition with an action (ADR 0035, S20-05): wait for its date, propose it, wait until
        it is SETTLED (decided by a person or by the policy, then played by its `ActionWorkflow`,
        never here), and judge `action_succeeded` with the other guarantees."""
        spec = transition.action
        if spec.not_before and not await self._wait_for_the_date(spec.not_before):
            return None  # stopped, or a migration to apply: the loop sees to it
        proposed = await execute_on_its_queue(
            named(
                action_activities.propose_transition_action, action_activities.proposer_l_action_de_transition
            ),
            {
                "project_id": params.project_id,
                "work_item_id": params.work_item_id,
                "transition": transition.key,
                "attempt": attempt,
                "workflow_id": workflow.info().workflow_id,
                "action": spec.model_dump(mode="json", by_alias=True, exclude_none=True),
            },
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_RETRY,
        )
        refusal = read_key(proposed, "refusal")
        if refusal:
            refusal_outcome = GateOutcome(
                "action_succeeded", False, detail=f"the action cannot be proposed: {refusal}"
            )
            return engine.after_gates(transition, [refusal_outcome], self._counted_attempts(transition.key))
        action_id, action_status = str(proposed["action_id"]), str(proposed["status"])
        self.current_action = action_id
        while action_status not in ACTION_REGLEE and not self.stopped:
            await wait_signal(lambda: action_id in self.settled_actions or self.stopped, ACTION_REREAD)
            if action_id in self.settled_actions:
                action_status = self.settled_actions.pop(action_id)
            elif not self.stopped:
                reread = await execute_on_its_queue(
                    named(action_activities.action_state, action_activities.etat_de_l_action),
                    action_id,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=DEFAULT_RETRY,
                )
                action_status = str(reread["status"])
        self.current_action = None
        if self.stopped:
            return None
        other_gates = transition.model_copy(
            update={"gates": [g for g in transition.gates if g.name != "action_succeeded"]}
        )
        outcomes = await self._gates(params, other_gates, self.last_run or "")
        return engine.after_action(
            transition, action_status, outcomes, self._counted_attempts(transition.key)
        )

    async def _run_spawn(self, params: InterpreterInput, engine: WorkflowEngine, transition: Any) -> Any:
        """A fan-out and its join (S38-12, ADR 0057): open the children, then wait until every one is
        settled — woken by `child_settled`, re-reading them every `CHILDREN_REREAD` in case a signal
        is lost —, and judge the join with the transition's other gates. Opening is idempotent (the
        children's keys are the parent's with a rank): a migration or a retry finds them again."""
        spec = transition.spawn.model_dump(mode="json")
        opened = await execute_on_its_queue(
            children_activities.spawn_children,
            {
                "project_id": params.project_id,
                "work_item_id": params.work_item_id,
                "workflow_id": workflow.info().workflow_id,
                "spawn": spec,
            },
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=DEFAULT_RETRY,
        )
        if opened.get("refused"):
            refused = GateOutcome(
                "children_settled", False, detail=f"the children cannot be opened: {opened['refused']}"
            )
            return engine.after_gates(transition, [refused], self._counted_attempts(transition.key))
        children = list(opened["children"])
        while not self.stopped and self.workflow_override is None:
            seen = self.children_settled
            state = await execute_on_its_queue(
                children_activities.children_state,
                {"work_item_id": params.work_item_id, "children": children, "spawn": spec},
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=DEFAULT_RETRY,
            )
            if not state["pending"]:
                break
            self.waiting = f"{len(state['pending'])} of {len(children)} child work items not settled"
            await wait_signal(
                lambda seen=seen: (
                    self.children_settled != seen or self.stopped or self.workflow_override is not None
                ),
                CHILDREN_REREAD,
            )
        self.waiting = None
        if self.stopped or self.workflow_override is not None:
            return None  # stopped, or a migration to apply: the loop sees to it
        joined = GateOutcome("children_settled", bool(state["passed"]), detail=str(state["detail"]))
        outcomes = await self._gates(params, transition, self.last_run or "")
        return engine.after_gates(transition, [joined, *outcomes], self._counted_attempts(transition.key))

    async def _wait_for_the_date(self, expression: str) -> bool:
        """Wait for `not_before`, RECOMPUTED at every change of the fields: moving the date re-arms
        the timer, bringing it before the present time makes it leave. A missing or unreadable
        field: we wait for it to be filled in, we do not leave without a date. False when the
        ticket stops or a migration interrupts it."""
        while not self.stopped and self.workflow_override is None:
            seen_version = self.fields_version
            try:
                target = echeance(expression, self.fields)
            except DateIllisible:
                target = None
            now = workflow.now()
            if target is not None and target <= now:
                if self.paused:  # the date passed during a pause: we wait for the resumption
                    await workflow.wait_condition(
                        lambda: not self.paused or self.stopped or self.workflow_override is not None
                    )
                    continue
                self.waiting = None
                return True
            self.waiting = target.isoformat() if target is not None else f"{expression} (unknown)"
            await wait_signal(
                lambda seen_version=seen_version: (
                    self.fields_version != seen_version or self.stopped or self.workflow_override is not None
                ),
                (target - now) if target is not None else None,
            )
        self.waiting = None
        return False
