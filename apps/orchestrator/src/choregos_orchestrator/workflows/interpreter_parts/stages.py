# SPDX-License-Identifier: Apache-2.0
"""The stage of an agent or a job: prepare, start, await, decide.

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
    from choregos_core.gates import unchanged_run

    from ...activities import scm as scm_activities
    from ...activities import stage as stage_activities
    from ...activities.interpretation import (
        message_de,
    )

from ..interpreter import (
    ALREADY_FIXED_ON_EVIDENCE,
    BUDGET_NEVER_RETRIES,
    BUDGET_RESERVATION,
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
            workflow.logger.warning("run diff not collected: %s", message_de(exc))
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
