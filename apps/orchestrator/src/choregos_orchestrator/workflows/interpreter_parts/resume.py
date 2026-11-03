# SPDX-License-Identifier: Apache-2.0
"""The resumption of a parked ticket.

Methods moved as they were from `workflows/interpreter.py`; the handlers stay on the class."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow

from ..scheduling import execute_on_its_queue

with workflow.unsafe.imports_passed_through():
    from choregos_core import Decision, WorkflowEngine

    from ...activities import tracker as tracker_activities
    from ...activities.interpretation import (
        escalation_context,
    )

from ..interpreter import (
    ANSWER_GRANTS_ONE_ATTEMPT,
    ESCALATION_CONTEXT_AFTER_RESTART,
    RERUN_GRANTS_ONE_ATTEMPT,
    RESUME_AFTER_ESCALATION,
    _attempts_ceiling,
)
from .base import InterpreterBase
from .helpers import DEFAULT_RETRY


class ResumeMixin(InterpreterBase):
    """The resumption of a parked ticket."""

    def _note_the_resume_point(
        self, engine: WorkflowEngine, transition: Any, kind: str, decision: Any
    ) -> None:
        """A step that parks the ticket (question, escalation) says where it will resume."""
        if (
            kind != "human"
            and decision.next_state != transition.from_
            and (decision.escalated or decision.next_state == engine.question_state())
        ):
            self.resume_point = (decision.next_state, transition.from_, transition.key)

    def _counted_attempts(self, key: str) -> int:
        """The attempts that count for `max_attempts`: those after the last resumption."""
        return self.attempts[key] - self.attempt_resets[key]

    def _is_parked(self) -> bool:
        return self.resume_point is not None and self.resume_point[0] == self.state

    def _rerun_playable(self) -> bool:
        """ "Replay the stage" only plays on a parked ticket; elsewhere, it is forgotten."""
        if self.rerun_requested and not self._is_parked() and not self.resume_lookup_pending:
            self.rerun_requested = False
        return self.rerun_requested

    def _look_up_resume(self) -> None:
        """A request to resume a work item this interpreter did not park — it was started again
        from the database (#497): where it resumes is to be read before the request plays."""
        if self._is_parked():
            return
        # The condition first, `patched` next — and one id per request (see the marker).
        self.resume_lookups += 1
        if workflow.patched(f"{ESCALATION_CONTEXT_AFTER_RESTART}-{self.resume_lookups}"):
            self.resume_lookup_pending = True

    async def _replay_requested(self) -> bool:
        """`_rerun_accepted`, once a restarted interpreter has read where its work item resumes —
        which an answer waiting in `decisions` needs as well."""
        if self.resume_lookup_pending:
            await self._read_the_parking()
        return self._rerun_accepted()

    async def _read_the_parking(self) -> None:
        """Rebuilds `resume_point` from what the escalation left in the database (S22-41), and the
        transition's attempts: the replayed stage gets a new run number, not the failed run's."""
        self.resume_lookup_pending = False
        if self._is_parked():
            return
        found = await execute_on_its_queue(
            escalation_context,
            {"project_id": self.project_id, "work_item_id": self.work_item_id, "state": self.state},
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_RETRY,
        )
        parked = found.get("parked")
        if not parked or parked[0] != self.state:
            return
        self.resume_point = (str(parked[0]), str(parked[1]), str(parked[2]))
        key = self.resume_point[2]
        self.attempts[key] = max(self.attempts[key], int(found.get("attempts") or 0))

    def _rerun_accepted(self) -> bool:
        """Does the requested rerun play? If not it is FORGOTTEN: left in place, it kept the wait
        condition true, and `_wait_human` consumed its hour-long delay at each turn, so on the dev
        tenant, on 9 October, an abandon request was recreated every 4 s.

        That is the case of a history where `patched` already answered no: the SDK MEMOISES the
        answer for the whole execution, and a ticket that had received an answer before the marker
        never takes the new path. It must then be reset before that answer."""
        if self._rerun_playable() and workflow.patched(RESUME_AFTER_ESCALATION):
            return True
        self.rerun_requested = False
        return False

    def _answer_resumes(self, decision: dict[str, Any]) -> bool:
        """An ANSWER to a question, on a parked ticket: `on_answer: resume`."""
        return (
            decision.get("kind") == "question"
            and self._is_parked()
            and workflow.patched(RESUME_AFTER_ESCALATION)
        )

    def _rerun_allowance(self, engine: WorkflowEngine, key: str) -> None:
        """A rerun leaves the stage ONE attempt before its cap (S27-13): the replayed stage plays
        once, and escalates again if it fails. Attempts spent before count, whatever the number of
        reruns, so `max_attempts` bounds the stage plus one attempt per explicit rerun."""
        transition = next((t for t in engine.wf.transitions if t.key == key), None)
        if transition is None:
            self.attempt_resets[key] = self.attempts[key]
            return
        allowed = engine.max_attempts(transition)
        ceiling = _attempts_ceiling({"attempts_ceilings": self.ceilings}, engine.agent_of(transition))
        if ceiling is not None:
            allowed = min(allowed, ceiling)
        self.attempt_resets[key] = max(self.attempt_resets[key], self.attempts[key] - (allowed - 1))

    async def _resume(self, why: str, *requests: Any, engine: WorkflowEngine | None = None) -> Decision:
        """Brings the parked work item back to the state it left: pending requests are closed (the
        abandon offered is moot), and the stage gets one more attempt, not a new allowance."""
        assert self.resume_point is not None
        _, origin, key = self.resume_point
        answer = next((d for d in requests if isinstance(d, dict)), None)
        for request_id in dict.fromkeys(d for d in requests if isinstance(d, str) and d):
            await execute_on_its_queue(
                tracker_activities.close_human_request,
                {
                    "request_id": request_id,
                    "decided_by": (answer or {}).get("decided_by", "the platform"),
                    "decision": answer
                    if answer and answer.get("request_id") == request_id
                    else {"approved": False, "superseded": f"the work item resumed ({why})"},
                },
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=DEFAULT_RETRY,
            )
        self.pending_request = None
        self.rerun_requested = False
        self.resume_point = None
        # The condition first, `patched` next: a rerun asks its marker, an answer its own.
        marker = ANSWER_GRANTS_ONE_ATTEMPT if answer is not None else RERUN_GRANTS_ONE_ATTEMPT
        if engine is not None and workflow.patched(marker):
            self._rerun_allowance(engine, key)
        else:
            self.attempt_resets[key] = self.attempts[key]
        return Decision(next_state=origin, reason=f"{why}: resumes at `{origin}`")
