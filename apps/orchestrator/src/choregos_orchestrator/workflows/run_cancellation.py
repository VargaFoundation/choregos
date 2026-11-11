# SPDX-License-Identifier: Apache-2.0
"""`RunCancellation`: a run a person stopped, stopped on its executor (ADR 0142 §2).

The API writes the run's result as stopped by a person, then starts this workflow once that is
committed — one per run, `run-cancel-<run id>`. It plays the `cancel_run` activity on the executor's
queue: the run's logs archived, its executor told to stop, its key closed and its spend booked once
(ADR 0008). The interpreter that awaited the run reads the written result at its next poll; it does
not wait for this workflow, and this workflow does not decide anything about the work item.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow

from .interpreter import DEFAULT_RETRY
from .scheduling import execute_on_its_queue

with workflow.unsafe.imports_passed_through():
    from ..activities import stage as stage_activities


@workflow.defn(name="RunCancellation", sandboxed=False)
class RunCancellation:
    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        ids = {"project_id": str(payload["project_id"]), "run_id": str(payload["run_id"])}
        await execute_on_its_queue(
            stage_activities.cancel_run,
            ids,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=DEFAULT_RETRY,
        )
        return {"run_id": ids["run_id"], "cancelled": True}
