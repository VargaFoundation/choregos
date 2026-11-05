# SPDX-License-Identifier: Apache-2.0
"""A transition a person decides: approval or task.

Methods moved as they were from `workflows/interpreter.py`; the handlers stay on the class."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow

from ..scheduling import execute_on_its_queue

with workflow.unsafe.imports_passed_through():
    from choregos_core import WorkflowEngine

    from ...activities import tracker as tracker_activities

from ..interpreter import HUMAN_TASKS, HUMAN_WAIT_YIELDS_TO_MIGRATION
from .base import InterpreterBase
from .helpers import DEFAULT_RETRY, NO_RETRY, InterpreterInput, _decision, wait_signal


class HumansMixin(InterpreterBase):
    """A transition a person decides: approval or task."""

    async def _wait_human(self, params: InterpreterInput, engine: WorkflowEngine, transition: Any) -> Any:
        actor = engine.human_of(transition)
        sla_hours = (actor.sla_hours if actor else None) or 24
        is_task = transition.task is not None and workflow.patched(HUMAN_TASKS)
        if is_task:
            kind, payload = (
                "task",
                {
                    "summary": transition.task.title or f"Task before `{transition.to}`",
                    "instructions": transition.task.instructions,
                    "form": transition.task.form,
                    "attest": transition.task.attest,
                },
            )
        else:
            kind, payload = "approval", {"summary": f"Approval needed to move to `{transition.to}`"}
        request = await self._create_request(
            params,
            transition,
            kind=kind,
            payload=payload,
            sla_hours=sla_hours,
        )
        self.pending_request = request["request_id"]
        timeout = timedelta(hours=transition.timeout_hours or sla_hours * 3)
        reminder_sent = False
        waited = timedelta()
        step = timedelta(hours=1)
        # Asked once, before the first wait: an older history never woke on a migration here.
        yields = workflow.patched(HUMAN_WAIT_YIELDS_TO_MIGRATION)
        while waited < timeout:
            got = await wait_signal(
                lambda: (
                    bool(self.decisions)
                    or self.stopped
                    or self._rerun_playable()
                    or (yields and bool(self.migration))
                ),
                min(step, timeout - waited),
            )
            if self.stopped:
                return None
            if yields and self.migration:
                await self._yield_to_migration()
                return None
            if self._suspension_left() == timedelta(0):
                # `sandbox.suspend_max_hours` passed: the sandbox goes (the executor's platform
                # deletes it at that instant anyway), and the answer will replay the stage.
                await self._release_suspension()
            if await self._replay_requested():
                return await self._resume("replay requested", self.pending_request, engine=engine)
            if self.decisions:
                decision = self.decisions.popleft()
                if self._answer_resumes(decision):
                    if self.suspension is not None:
                        self.suspension["answered"] = True
                    return await self._resume(
                        "answered", decision.get("request_id"), decision, self.pending_request, engine=engine
                    )
                await execute_on_its_queue(
                    tracker_activities.close_human_request,
                    {
                        "request_id": decision.get("request_id") or self.pending_request,
                        "decided_by": decision.get("decided_by", "a person"),
                        "decision": decision,
                    },
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=DEFAULT_RETRY,
                )
                self.pending_request = None
                approved = bool(decision.get("approved"))
                if is_task and approved and decision.get("values"):
                    # The task's values are fields: the API has already written them, an action
                    # date that depends on them is recomputed here.
                    self.fields = {**self.fields, **dict(decision["values"])}
                    self.fields_version += 1
                return engine.after_human(transition, approved, decision.get("reason") or "")
            waited += step
            if not got and not reminder_sent and waited >= timedelta(hours=sla_hours):
                reminder_sent = True
                await self._remind(params, actor, transition)
        escalate = engine.timeout_state() or transition.from_
        return (
            engine.after_human(transition, approved=False, reason="no human decision in time")
            if transition.on_reject
            else _decision(escalate, "no human decision in time")
        )

    async def _yield_to_migration(self) -> None:
        """A re-route (ADR 0123) arrived while a person was asked: the request is closed as superseded
        — it judges no decision, leaves no feedback — a suspended sandbox is released, and the main
        loop migrates (`_migrate_by_definition`)."""
        by = str(self.migration.get("by") or "the platform")
        await execute_on_its_queue(
            tracker_activities.close_human_request,
            {
                "request_id": self.pending_request,
                "decided_by": by,
                "decision": {"approved": False, "superseded": "the work item changed workflow"},
            },
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_RETRY,
        )
        self.pending_request = None
        await self._release_suspension()

    async def _remind(self, params: InterpreterInput, actor: Any, transition: Any) -> None:
        from choregos_core import Message

        sla = actor.sla_hours if actor else 24

        await execute_on_its_queue(
            tracker_activities.notify,
            {
                "project_id": params.project_id,
                "message": Message(
                    title=f"Reminder: {params.tracker_key} is waiting for a decision",
                    body=f"Transition `{transition.key}` has been waiting for {sla} h.",
                    severity="warning",
                ).model_dump(mode="json"),
            },
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=NO_RETRY,
        )

    async def _create_request(
        self,
        params: InterpreterInput,
        transition: Any,
        *,
        kind: str,
        payload: dict[str, Any],
        sla_hours: int = 24,
    ) -> dict[str, Any]:
        # The scheduling wrapper returns an `Any` where `execute_activity` had a typed overload:
        # going through an annotated name keeps the signature's promise.
        created: dict[str, Any] = await execute_on_its_queue(
            tracker_activities.create_human_request,
            {
                "project_id": params.project_id,
                "work_item_id": params.work_item_id,
                "transition_id": transition.key,
                "kind": kind,
                "payload": payload,
                "sla_hours": sla_hours,
            },
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_RETRY,
        )
        return created

    async def _request_scope_change(self, params: InterpreterInput, transition: Any, request: Any) -> None:
        await self._create_request(
            params,
            transition,
            kind="scope_change",
            payload={"paths": list(request.paths), "justification": request.justification},
        )
