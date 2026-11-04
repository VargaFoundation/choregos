# SPDX-License-Identifier: Apache-2.0
"""A typed refusal of an activity escalates the work item.

Methods moved as they were from `workflows/interpreter.py`; the handlers stay on the class."""

from __future__ import annotations

from typing import Any

from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from choregos_core import WorkflowEngine

    from ...activities import scm as scm_activities
    from ...activities.execution import START_REFUSED
    from ...activities.interpretation import (
        message_of,
    )

from ..interpreter import (
    OPEN_PR_WITHOUT_BRANCH_ESCALATES,
    STAGE_CONFIG_REFUSAL_ESCALATES,
    START_REFUSAL_ESCALATES,
)
from .base import InterpreterBase
from .helpers import CONFIGURATION_REFUSALS, InterpreterInput, Refusal


class RefusalsMixin(InterpreterBase):
    """A typed refusal of an activity escalates the work item."""

    async def _refused(
        self, params: InterpreterInput, engine: WorkflowEngine, transition: Any, reason: str, detail: str
    ) -> Any:
        """The stage did not start (ADR 0047) — too little budget left, or the work item ran every
        stage the policy allows: no key, no run, a person decides."""
        if reason == "max_stages":
            decision = engine.after_stage_cap(transition, detail[:200])
        else:
            decision = engine.after_budget_exceeded(transition, detail[:200])
        if decision.next_state == engine.question_state():
            await self._create_request(
                params,
                transition,
                kind="approval",
                payload={"question": detail, "options": [], "summary": detail, "reason": reason},
            )
        return decision

    async def _configuration_refused(
        self, params: InterpreterInput, engine: WorkflowEngine, transition: Any, error: ActivityError
    ) -> Any:
        """`prepare_stage` refused the stage on its configuration (#543): the work item escalates,
        as after a failed attempt, with the refusal as its reason, and waits for an operator to fix
        the configuration and replay the stage. `None` — the interpreter fails, as before — for any
        other error, for a history recorded before the marker, and for a transition with nowhere
        to escalate to: going back to the state it started from would refuse again, forever."""
        detail = message_of(error)
        return await self._refusal_escalates(
            params,
            engine,
            transition,
            error,
            Refusal(
                types=CONFIGURATION_REFUSALS,
                marker=STAGE_CONFIG_REFUSAL_ESCALATES,
                reason=f"configuration refused: {detail}",
                question=f"The stage `{transition.key}` cannot start: {detail}. "
                "Fix the configuration, then replay the stage.",
                kind="configuration",
            ),
        )

    async def _start_refused(
        self, params: InterpreterInput, engine: WorkflowEngine, transition: Any, error: ActivityError
    ) -> Any:
        """The executor refused the run on the deployment's configuration (#750, S38-13e): the run is
        closed `failed` with the reason, and the work item escalates at once with it — another attempt
        would be refused the same way. Any other error of `start_run` is `None`, and fails the
        interpreter as before."""
        detail = message_of(error)
        return await self._refusal_escalates(
            params,
            engine,
            transition,
            error,
            Refusal(
                types=frozenset({START_REFUSED}),
                marker=START_REFUSAL_ESCALATES,
                reason=f"the executor refused the run: {detail}",
                question=f"The stage `{transition.key}` could not start: {detail}. "
                "Fix the deployment or the policy, then replay the stage.",
                kind="configuration",
            ),
        )

    async def _no_branch_refused(
        self, params: InterpreterInput, engine: WorkflowEngine, transition: Any, error: ActivityError
    ) -> Any:
        """`open_pull_request` found no branch to open a pull request from (S22-46, #539): the
        implementation changed nothing. The work item escalates with that reason, instead of the
        interpreter failing on GitHub's 422. Any other error — a 403, a 5xx once its retries are
        spent — is `None`, and fails the interpreter as before."""
        detail = message_of(error)
        return await self._refusal_escalates(
            params,
            engine,
            transition,
            error,
            Refusal(
                types=frozenset({scm_activities.NO_BRANCH}),
                marker=OPEN_PR_WITHOUT_BRANCH_ESCALATES,
                reason=detail,
                question=f"`{transition.key}` cannot open a pull request: {detail}. "
                "Check the work item (already fixed?), then close it or replay the implementation.",
                kind="no_branch",
            ),
        )

    async def _refusal_escalates(
        self,
        params: InterpreterInput,
        engine: WorkflowEngine,
        transition: Any,
        error: ActivityError,
        refusal: Refusal,
    ) -> Any:
        """A typed, non-retryable refusal escalates the work item, as after a failed attempt, with
        its reason; a request asks its question when it lands on the question state. `None` for any
        other error, for a history recorded before its marker, and for a transition with nowhere to
        escalate to: going back to the state it started from would refuse again, forever."""
        cause = error.cause
        if not (isinstance(cause, ApplicationError) and cause.non_retryable and cause.type in refusal.types):
            return None
        decision = engine.escalate(transition, refusal.reason[:300])
        # The condition first, `patched` next: an older history replayed here answers no without
        # the marker being asked for an error the old code did not escalate.
        if decision.next_state == transition.from_ or not workflow.patched(refusal.marker):
            return None
        detail = message_of(error)
        if decision.next_state == engine.question_state():
            await self._create_request(
                params,
                transition,
                kind="approval",
                payload={
                    "question": refusal.question,
                    "options": [],
                    "summary": detail,
                    "reason": refusal.kind,
                },
            )
        return decision
