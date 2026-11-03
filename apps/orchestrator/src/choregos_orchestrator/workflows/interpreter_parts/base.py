# SPDX-License-Identifier: Apache-2.0
"""What the mixins of `WorkflowInterpreter` read from the class that holds them.

Typing only: the attributes are set by `WorkflowInterpreter.__init__`, and the method stubs below
are never defined at run time (the mixins define the real ones).
"""

from __future__ import annotations

from collections import Counter, deque
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from temporalio import workflow
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from choregos_core import Decision, WorkflowEngine

from .helpers import InterpreterInput, Refusal


class InterpreterBase:
    """The state and the cross-mixin calls, declared for the type checker."""

    inbox: deque[dict[str, Any]]
    decisions: deque[dict[str, Any]]
    attempts: Counter[str]
    state: str
    cost_usd: float
    paused: bool
    stopped: bool
    current_run: str | None
    last_run: str | None
    pending_request: str | None
    last_outcome: str | None
    last_outputs: dict[str, Any] | None
    workflow_override: dict[str, Any] | None
    state_mapping: dict[str, str]
    migration: dict[str, Any]
    migrations: int
    findings_count: int
    fields: dict[str, Any]
    fields_version: int
    settled_actions: dict[str, str]
    waiting: str | None
    current_action: str | None
    failure_signatures: dict[str, str]
    resume_point: tuple[str, str, str] | None
    attempt_resets: Counter[str]
    rerun_requested: bool
    ceilings: dict[str, Any]
    suspension: dict[str, Any] | None
    resumptions: int
    resume_lookup_pending: bool
    resume_lookups: int
    project_id: str
    work_item_id: str
    children_settled: int
    ledger_run: str | None

    if TYPE_CHECKING:

        def _counted_attempts(self, key: str) -> int: ...

        async def _gates(self, params: InterpreterInput, transition: Any, run_id: str) -> list[Any]: ...

        async def _resume(
            self, why: str, *requests: Any, engine: WorkflowEngine | None = None
        ) -> Decision: ...

        async def _create_request(
            self,
            params: InterpreterInput,
            transition: Any,
            *,
            kind: str,
            payload: dict[str, Any],
            sla_hours: int = 24,
        ) -> dict[str, Any]: ...

        def _absorb(self, event: dict[str, Any]) -> None: ...

        def _suspension_left(self) -> timedelta | None: ...

        def _rerun_playable(self) -> bool: ...

        async def _replay_requested(self) -> bool: ...

        async def _release_suspension(self) -> None: ...

        def _answer_resumes(self, decision: dict[str, Any]) -> bool: ...

        async def _suspend(self, params: InterpreterInput, run_id: str, key: str) -> None: ...

        async def _start_refused(
            self, params: InterpreterInput, engine: WorkflowEngine, transition: Any, error: ActivityError
        ) -> Any: ...

        async def _resume_suspension(self, params: InterpreterInput, key: str) -> tuple[str, Any] | None: ...

        async def _request_scope_change(
            self, params: InterpreterInput, transition: Any, request: Any
        ) -> None: ...

        async def _refused(
            self, params: InterpreterInput, engine: WorkflowEngine, transition: Any, reason: str, detail: str
        ) -> Any: ...

        async def _no_branch_refused(
            self, params: InterpreterInput, engine: WorkflowEngine, transition: Any, error: ActivityError
        ) -> Any: ...

        def _is_parked(self) -> bool: ...

        async def _configuration_refused(
            self, params: InterpreterInput, engine: WorkflowEngine, transition: Any, error: ActivityError
        ) -> Any: ...

        async def _refusal_escalates(
            self,
            params: InterpreterInput,
            engine: WorkflowEngine,
            transition: Any,
            error: ActivityError,
            refusal: Refusal,
        ) -> Any: ...

        async def _already_fixed(
            self,
            params: InterpreterInput,
            engine: WorkflowEngine,
            transition: Any,
            result: Any,
            run_id: str,
            decision: Any,
        ) -> Any: ...

        def _identical_failure(
            self, engine: WorkflowEngine, transition: Any, result: Any, decision: Any
        ) -> Any: ...

        def _look_up_resume(self) -> None: ...

        async def _mirror(self, params: InterpreterInput, reason: str) -> None: ...

        async def _wait_for_the_date(self, expression: str) -> bool: ...

        async def _run_action(
            self, params: InterpreterInput, engine: WorkflowEngine, transition: Any, attempt: int
        ) -> Any: ...

        async def _run_spawn(
            self, params: InterpreterInput, engine: WorkflowEngine, transition: Any
        ) -> Any: ...

        def _note_the_resume_point(
            self, engine: WorkflowEngine, transition: Any, kind: str, decision: Any
        ) -> None: ...
