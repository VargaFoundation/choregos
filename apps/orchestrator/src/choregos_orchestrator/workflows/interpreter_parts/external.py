# SPDX-License-Identifier: Apache-2.0
"""Migration, waits for external events and the mirror of the state.

Methods moved as they were from `workflows/interpreter.py`; the handlers stay on the class."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.exceptions import ActivityError, is_cancelled_exception

from ..activity_names import english_payload_keys
from ..scheduling import execute_on_its_queue

with workflow.unsafe.imports_passed_through():
    from choregos_contracts import Workflow
    from choregos_core import WorkflowEngine

    from ...activities import tracker as tracker_activities
    from ...activities.interpretation import (
        record_migration,
    )

from ..interpreter import ESCALATION_CONTEXT_STORED, MIGRATION_BY_DEFINITION, STATUS_COMMENT_BEST_EFFORT
from .base import InterpreterBase
from .helpers import _MIGRATION, DEFAULT_RETRY, InterpreterInput


class ExternalMixin(InterpreterBase):
    """Migration, waits for external events and the mirror of the state."""

    async def _migrate_by_definition(
        self, params: InterpreterInput, engine: WorkflowEngine
    ) -> WorkflowEngine:
        """`migrate`: the target definition arrives in the signal, read and checked by the API.

        The ticket's pin follows it (a `continue_as_new` will reload it). A migration that became
        impossible (the state moved since the API's check) leaves the ticket alive, on its
        definition, and records it: `_migrate` used to kill it by raising inside the workflow.
        """
        requested, self.migration, self.workflow_override = self.migration, {}, None
        self.migrations += 1
        info = workflow.info()
        # S45-39: the keys are English for a run started after it, the recorded ones before.
        key_name, refusal_name = (
            ("key", "refusal") if english_payload_keys() else ("cle", "refus")
        )  # lang: fr-ok
        record = {
            "project_id": params.project_id,
            "work_item_id": params.work_item_id,
            key_name: f"{info.workflow_id}/{info.run_id}/{self.migrations}",
            "from_state": self.state,
        }
        try:
            target = Workflow.model_validate(requested.get("workflow") or {})
            new_state = engine.can_migrate_to(target, self.state, dict(requested.get("state_mapping") or {}))
        except ValueError as refusal:  # a pydantic ValidationError is one
            await execute_on_its_queue(
                record_migration,
                {**record, refusal_name: str(refusal)[:2000]},
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=DEFAULT_RETRY,
            )
            return engine
        await execute_on_its_queue(
            record_migration,
            {
                **record,
                "to_state": new_state,
                "workflow_def_id": requested.get("workflow_def_id"),
                "workflow": f"{target.metadata.name}@{target.metadata.version}",
            },
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_RETRY,
        )
        self.state = new_state
        await self._mirror(params, f"migrated to {target.metadata.name}@{target.metadata.version}")
        return WorkflowEngine(target)

    def _migrate(self, engine: WorkflowEngine) -> WorkflowEngine:
        """Old path, kept to replay the histories from before `MIGRATION_BY_DEFINITION`."""
        override = self.workflow_override or {}
        self.workflow_override, self.migration = None, {}
        definition = override.get("workflow") or override.get("json") or override
        target = Workflow.model_validate(definition)
        new_engine = WorkflowEngine(target)
        self.state = WorkflowEngine(engine.wf).can_migrate_to(target, self.state, self.state_mapping)
        return new_engine

    async def _wait_external(self, engine: WorkflowEngine, context: dict[str, Any]) -> str | None:
        """A waiting state with no outgoing transition: the human board decides.

        A requested migration wakes it too (`_MIGRATION`): a parked ticket has no other decision
        point, and would otherwise wait for an event that may never come. A history from before
        `MIGRATION_BY_DEFINITION` was not woken: it replays waiting for the next event, as then.
        """
        while not self.stopped:
            left = self._suspension_left()
            try:
                await workflow.wait_condition(
                    lambda: (
                        bool(self.inbox)
                        or bool(self.decisions)
                        or self.stopped
                        or bool(self.migration)
                        or self._rerun_playable()
                    ),
                    timeout=left,
                )
            except TimeoutError:
                # `sandbox.suspend_max_hours` passed without an answer: the sandbox goes, and
                # the answer, when it comes, replays the stage.
                await self._release_suspension()
                continue
            if await self._replay_requested():
                return (
                    await self._resume("replay requested", self.pending_request, engine=engine)
                ).next_state
            if self.migration and not (self.inbox or self.decisions or self.stopped):
                if workflow.patched(MIGRATION_BY_DEFINITION):
                    return _MIGRATION
                await workflow.wait_condition(
                    lambda: bool(self.inbox) or bool(self.decisions) or self.stopped
                )
            while self.inbox:
                event = self.inbox.popleft()
                if event.get("type") == "tracker.item.moved":
                    to_status = event.get("payload", {}).get("to_status")
                    for name, state in engine.wf.states.items():
                        if state.tracker and state.tracker.status == to_status:
                            return name
                self._absorb(event)
            while self.decisions:
                decision = self.decisions.popleft()
                if self._answer_resumes(decision):
                    if self.suspension is not None:
                        self.suspension["answered"] = True
                    resumed = await self._resume(
                        "answered", decision.get("request_id"), decision, engine=engine
                    )
                    return resumed.next_state
                if decision.get("approved"):
                    transitions = engine.wf.transitions_from(self.state)
                    if transitions:
                        return transitions[0].to
        return None

    async def _mirror(self, params: InterpreterInput, reason: str) -> None:
        mirrored: dict[str, Any] = {
            "project_id": params.project_id,
            "work_item_id": params.work_item_id,
            "state": self.state,
            "reason": reason,
        }
        if (
            self.resume_point is not None
            and self._is_parked()
            and workflow.patched(ESCALATION_CONTEXT_STORED)
        ):
            mirrored["parked"] = list(self.resume_point)  # where it resumes, for a restarted interpreter
        await execute_on_its_queue(
            tracker_activities.mirror_state,
            mirrored,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_RETRY,
        )
        # `mirror_state` stays blocking: it writes the work item's state in the same transaction as
        # the board, and a state the platform did not record is not one to go on from.
        try:
            await execute_on_its_queue(
                tracker_activities.update_status_comment,
                {"project_id": params.project_id, "work_item_id": params.work_item_id},
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=DEFAULT_RETRY,
            )
        except ActivityError as error:
            if is_cancelled_exception(error) or not workflow.patched(STATUS_COMMENT_BEST_EFFORT):
                raise
            # Only a mirror of what the platform holds: the next `_mirror` rewrites it whole.
            workflow.logger.warning(
                "status comment not updated, the work item goes on",
                extra={
                    "project": params.project_slug,
                    "work_item": params.work_item_id,
                    "state": self.state,
                    "error": str(error.cause or error),
                },
            )
