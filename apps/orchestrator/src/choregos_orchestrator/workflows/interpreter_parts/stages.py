# SPDX-License-Identifier: Apache-2.0
"""The stage of an agent, a job or a decision: prepare, start, await, decide.

Methods moved as they were from `workflows/interpreter.py`; the handlers stay on the class."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.exceptions import ActivityError

from ..scheduling import execute_on_its_queue

with workflow.unsafe.imports_passed_through():
    from choregos_contracts import StageResult, StageStatus
    from choregos_core import WorkflowEngine
    from choregos_core.decision_ledger import declares_a_confidence
    from choregos_core.gates import unchanged_run

    from ...activities import decision as decision_activities
    from ...activities import scm as scm_activities
    from ...activities import stage as stage_activities
    from ...activities.interpretation import (
        message_of,
    )

from ..interpreter import (
    ALREADY_FIXED_ON_EVIDENCE,
    BUDGET_NEVER_RETRIES,
    BUDGET_RESERVATION,
    DECISION_LEDGER,
    DECLARED_OUTPUTS_REQUIRED,
    IDENTICAL_FAILURE_ESCALATES,
    SUSPEND_AROUND_HUMAN,
    TICKET_SPEND_FROM_LEDGER,
    _attempts_ceiling,
)
from .base import InterpreterBase
from .helpers import DEFAULT_RETRY, NO_RETRY, POLL_RETRY, InterpreterInput


class StagesMixin(InterpreterBase):
    """The stage of an agent or a job: prepare, start, await, decide."""

    async def _run_stage(
        self, params: InterpreterInput, engine: WorkflowEngine, transition: Any, context: dict[str, Any]
    ) -> Any:
        if engine.agent_of(transition) is None and engine.job_of(transition) is None:
            return engine.retry_or_escalate(
                transition, self._counted_attempts(transition.key), "agent actor not found"
            )
        key = transition.key
        resumed = await self._resume_suspension(params, key)
        if resumed is not None:
            run_id, budget = resumed
            self.current_run = run_id
            # It was suspended because its executor can: asked again, it is suspended again.
            capabilities = ["resume", "suspend"]
        else:
            attempt = self.attempts[key]
            self.attempts[key] = attempt + 1

            stage = {
                "project_id": params.project_id,
                "work_item_id": params.work_item_id,
                "transition_id": key,
                "from_state": transition.from_,
                "to_state": transition.to,
                "actor": transition.by or "",
                "attempt": attempt + 1,
                "outputs": list(transition.outputs),
                "inputs": list(transition.inputs),
                "output_types": dict(transition.output_types),
            }
            stage |= engine.run_request(transition)
            prepare = execute_on_its_queue(
                stage_activities.prepare_stage,
                stage,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=DEFAULT_RETRY,
            )
            try:
                prepared = await prepare
            except ActivityError as error:
                escalated = await self._configuration_refused(params, engine, transition, error)
                if escalated is None:
                    raise
                return escalated
            if prepared.get("refused") and workflow.patched(BUDGET_RESERVATION):
                refused = prepared["refused"]
                return await self._refused(
                    params, engine, transition, str(refused["reason"]), str(refused["detail"])
                )
            run_id = prepared["run_id"]
            self.current_run = run_id
            budget = prepared["stage_input"]["budget"]

            try:
                started = await execute_on_its_queue(
                    stage_activities.start_run,
                    {
                        "project_id": params.project_id,
                        "work_item_id": params.work_item_id,
                        "stage_input": prepared["stage_input"],
                    },
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=DEFAULT_RETRY,
                )
            except ActivityError as error:
                escalated = await self._start_refused(params, engine, transition, error)
                if escalated is None:
                    raise
                self.current_run = None  # closed `failed` by `start_run`; nothing runs
                return escalated
            capabilities = list((started or {}).get("capabilities") or [])
        awaited = await execute_on_its_queue(
            stage_activities.await_run,
            {
                "project_id": params.project_id,
                "run_id": run_id,
                "timeout_minutes": int(budget["max_minutes"]) + 10,
            },
            start_to_close_timeout=timedelta(minutes=int(budget["max_minutes"]) + 20),
            # GLOBAL bound, all attempts included: without it, twenty resumptions of a
            # two-hour wait would make a ticket that never dies.
            schedule_to_close_timeout=timedelta(minutes=int(budget["max_minutes"]) + 90),
            heartbeat_timeout=timedelta(minutes=5),
            retry_policy=POLL_RETRY,
        )
        result = StageResult.model_validate(awaited["result"])

        spend = await execute_on_its_queue(
            stage_activities.collect_spend,
            {"project_id": params.project_id, "run_id": run_id},
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_RETRY,
        )
        if "ticket_cost_usd" in spend and workflow.patched(TICKET_SPEND_FROM_LEDGER):
            self.cost_usd = float(spend["ticket_cost_usd"])  # the ledger: models AND tools
        else:
            self.cost_usd += float(spend.get("cost_usd", 0.0))
        await execute_on_its_queue(
            stage_activities.record_run_outcome,
            {"project_id": params.project_id, "run_id": run_id, "result": awaited["result"]},
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_RETRY,
        )
        # The run's diff is what the front end SHOWS, not what the ticket depends on. A failure
        # here (SCM unreachable, project without a repository on a late image) must not kill a
        # stage that has just succeeded: it happened to RH-1 on 2026-09-24.
        try:
            await execute_on_its_queue(
                scm_activities.collect_run_artifacts,
                {"project_id": params.project_id, "work_item_id": params.work_item_id, "run_id": run_id},
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=NO_RETRY,
            )
        except ActivityError as exc:
            workflow.logger.warning("run diff not collected: %s", message_of(exc))
        self.current_run = None
        self.last_run = run_id  # `system` transitions evaluate their gates on this run
        # What the stage answered: the next selection reads it (S48-01). Taken from the result the
        # activity returned, so a replay reads the same values.
        self.last_outputs = result.outputs.model_dump(mode="json", exclude_none=True) or None

        outcomes = await self._gates(params, transition, run_id)

        ticket_budget = float(spend.get("ticket_budget_usd") or context.get("ticket_budget_usd", 0.0))
        if self.cost_usd > ticket_budget > 0:
            result = result.model_copy(
                update={
                    "status": StageStatus.NEEDS_HUMAN,
                    "reason": "budget",
                    "summary": f"{result.summary} (work item budget exceeded)",
                }
            )
        for request in result.scope_changes_requested:
            await self._request_scope_change(params, transition, request)

        if (
            result.reason == "budget"
            and result.status is not StageStatus.DONE
            and workflow.patched(BUDGET_NEVER_RETRIES)
        ):
            # A person decides; the ticket is parked, and a request tells them why.
            result = result.model_copy(update={"status": StageStatus.NEEDS_HUMAN})
            decision = engine.after_budget_exceeded(transition, result.summary[:200])
        else:
            decision = engine.after_stage(
                transition,
                result,
                self._counted_attempts(key),
                outcomes,
                require_outputs=workflow.patched(DECLARED_OUTPUTS_REQUIRED),
                ceiling=_attempts_ceiling(context, engine.agent_of(transition)),
            )
            decision = self._identical_failure(engine, transition, result, decision)
            decision = await self._already_fixed(params, engine, transition, result, run_id, decision)
        if decision.next_state == engine.question_state() and result.status is StageStatus.NEEDS_HUMAN:
            await self._create_request(
                params,
                transition,
                kind="question" if result.questions else "approval",
                payload={
                    "question": result.questions[0].text if result.questions else result.summary,
                    "options": list(result.questions[0].options) if result.questions else [],
                    "summary": result.summary,
                    "run_id": run_id,
                },
            )
            if (
                result.questions
                and {"suspend", "resume"} <= set(capabilities)
                and workflow.patched(SUSPEND_AROUND_HUMAN)
            ):
                await self._suspend(params, run_id, key)
        return decision

    async def _already_fixed(
        self,
        params: InterpreterInput,
        engine: WorkflowEngine,
        transition: Any,
        result: Any,
        run_id: str,
        decision: Any,
    ) -> Any:
        """A step that succeeded without changing anything (S22-47, #539): when its state declares an
        "already fixed" exit, that exit's guarantees are read on this run. Proven, the work item ends
        there — no test, review or pull request on an empty change; unproven, the step escalates
        with what the evidence lacks, and a person decides. Any other step keeps its decision."""
        if decision.next_state != transition.to or decision.escalated or decision.retried:
            return decision
        exit_ = engine.already_fixed_exit(transition.from_)
        # The condition first, `patched` next: a history without such an exit never asks.
        if exit_ is None or not unchanged_run(result) or not workflow.patched(ALREADY_FIXED_ON_EVIDENCE):
            return decision
        outcomes = await self._gates(params, exit_, run_id)
        decided = engine.after_unchanged_stage(transition, exit_, outcomes)
        if decided.escalated and decided.next_state == engine.question_state():
            await self._create_request(
                params,
                transition,
                kind="approval",
                payload={
                    "question": f"`{transition.key}` changed nothing: {decided.reason}. Close the work "
                    "item if it is already fixed, or replay the implementation.",
                    "options": [],
                    "summary": result.summary,
                    "run_id": run_id,
                    "reason": "no_change_unproven",
                },
            )
        return decided

    # ───────────────────────── the decision ledger (ADR 0121 §6) ─────────────────────────

    def _recorded_run(self, kind: str, transition: Any, decision: Any) -> str | None:
        """The run whose ledger records wait for the next transition: a decision step, or an agent
        that declared a confidence, whose outcome moved the work item where the step leads."""
        if (
            kind not in ("agent", "decision")
            or decision.escalated
            or decision.next_state != transition.to
            or not declares_a_confidence(self.last_outputs)
        ):
            return None
        return self.last_run

    async def _note_transition_taken(self, params: InterpreterInput, transition: Any) -> None:
        """The state the transition selected after a recorded step leads to, on the step's records.
        Nothing selected (the work item waits for a move from outside): nothing is written."""
        run_id, self.ledger_run = self.ledger_run, None
        # The condition first, `patched` next: a history without a recorded step never asks.
        if run_id is None or transition is None or not workflow.patched(DECISION_LEDGER):
            return
        await execute_on_its_queue(
            decision_activities.note_transition_taken,
            {"project_id": params.project_id, "run_id": run_id, "transition": transition.to},
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_RETRY,
        )

    # ───────────────────────── a decision step (ADR 0121) ─────────────────────────

    async def _run_decision(
        self, params: InterpreterInput, engine: WorkflowEngine, transition: Any, context: dict[str, Any]
    ) -> Any:
        """A decision model answers the step's questions through the project's gateway: one activity,
        no run to start or await. Its answers are the step's outputs, which `when` reads on the next
        transitions as it reads an agent's (ADR 0106); its gates and its `on_fail` apply as after a run.

        A failed decision keeps the outputs the step read: its next attempt is asked about the same
        state. A refusal of the configuration escalates the work item, as `prepare_stage`'s does."""
        actor = engine.decision_of(transition)
        assert actor is not None, "a decision step without its decision actor"
        key = transition.key
        attempt = self.attempts[key]
        self.attempts[key] = attempt + 1
        try:
            decided = await execute_on_its_queue(
                decision_activities.decide,
                decision_activities.decision_request(
                    project_id=params.project_id,
                    work_item_id=params.work_item_id,
                    transition=transition,
                    decision=actor,
                    attempt=attempt + 1,
                    last_outputs=self.last_outputs,
                ),
                start_to_close_timeout=timedelta(
                    seconds=actor.timeout_seconds + decision_activities.DECISION_MARGIN_S
                ),
                retry_policy=DEFAULT_RETRY,
            )
        except ActivityError as error:
            escalated = await self._configuration_refused(params, engine, transition, error)
            if escalated is None:
                raise
            return escalated
        run_id = str(decided["run_id"])
        result = StageResult.model_validate(decided["result"])
        self.cost_usd = float(decided["ticket_cost_usd"])  # the ledger, as after a run
        self.last_run = run_id
        if result.status is StageStatus.DONE:
            self.last_outputs = result.outputs.model_dump(mode="json", exclude_none=True) or None
        outcomes = await self._gates(params, transition, run_id)
        decision = engine.after_stage(
            transition,
            result,
            self._counted_attempts(key),
            outcomes,
            ceiling=_attempts_ceiling(context, actor),
        )
        decision = self._identical_failure(engine, transition, result, decision)
        if decision.escalated and decision.next_state == engine.question_state():
            await self._create_request(
                params,
                transition,
                kind="approval",
                payload={
                    "question": f"The decision `{key}` did not decide: {result.summary}",
                    "options": [],
                    "summary": result.summary,
                    "run_id": run_id,
                    "reason": result.reason or "decision",
                },
            )
        return decision

    def _identical_failure(self, engine: WorkflowEngine, transition: Any, result: Any, decision: Any) -> Any:
        """A retry of a stage that failed exactly like its previous attempt escalates (S27-07)."""
        from choregos_core.dsl.engine import failure_signature

        signature = failure_signature(result)
        previous = self.failure_signatures.pop(transition.key, None)
        # A success, or an escalation (a person takes over; a replay of the stage starts afresh):
        # nothing to compare the next attempt with.
        if signature is None or decision.escalated:
            return decision
        if decision.retried and signature == previous and workflow.patched(IDENTICAL_FAILURE_ESCALATES):
            return engine.escalate(
                transition, f"the same failure twice in a row, not retried again: {signature[:200]}"
            )
        self.failure_signatures[transition.key] = signature
        return decision
