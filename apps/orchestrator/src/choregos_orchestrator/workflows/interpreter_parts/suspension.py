# SPDX-License-Identifier: Apache-2.0
"""A sandbox kept suspended across a person's answer (S35-03).

Methods moved as they were from `workflows/interpreter.py`; the handlers stay on the class."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from temporalio import workflow

from ..scheduling import execute_on_its_queue

with workflow.unsafe.imports_passed_through():
    from ...activities import suspension as suspension_activities

from .base import InterpreterBase
from .helpers import DEFAULT_RETRY, InterpreterInput


class SuspensionMixin(InterpreterBase):
    """A sandbox kept suspended across a person's answer (S35-03)."""

    async def _suspend(self, params: InterpreterInput, run_id: str, key: str) -> None:
        """The agent asked a question: its sandbox waits, suspended, for the answer."""
        done = await execute_on_its_queue(
            suspension_activities.suspend_run,
            {"project_id": params.project_id, "run_id": run_id},
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_RETRY,
        )
        if done.get("suspended"):
            self.suspension = {"run_id": run_id, "key": key, "until": done["until"], "answered": False}

    def _suspension_left(self) -> timedelta | None:
        """How long the suspended sandbox may still wait; None when nothing is suspended."""
        if self.suspension is None:
            return None
        until = datetime.fromisoformat(str(self.suspension["until"]))
        return max(until - workflow.now(), timedelta(0))

    async def _release_suspension(self) -> None:
        """The sandbox will not be resumed: destroyed now rather than at its bound."""
        suspended, self.suspension = self.suspension, None
        if suspended is None:
            return
        await execute_on_its_queue(
            suspension_activities.release_suspended_run,
            {"project_id": self.project_id, "run_id": suspended["run_id"]},
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_RETRY,
        )

    async def _resume_suspension(self, params: InterpreterInput, key: str) -> tuple[str, Any] | None:
        """The answered question's run goes on, if its sandbox still waits: `(run_id, budget)`.
        None — and the stage replays as before — when nothing was suspended for this transition,
        the ticket came back another way (a replay asked for), or the sandbox is gone."""
        suspended = self.suspension
        if suspended is None:
            return None
        if suspended["key"] != key or not suspended.get("answered"):
            await self._release_suspension()
            return None
        self.suspension = None
        self.resumptions += 1
        resumed = await execute_on_its_queue(
            suspension_activities.resume_run,
            {
                "project_id": params.project_id,
                "run_id": suspended["run_id"],
                "answer_id": f"{suspended['run_id']}/{self.resumptions}",
            },
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=DEFAULT_RETRY,
        )
        if not resumed.get("resumed"):
            return None
        return str(suspended["run_id"]), resumed["budget"]
