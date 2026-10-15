# SPDX-License-Identifier: Apache-2.0
"""`KnowledgeSync`: one loop per knowledge source (S33-04, ADR 0051).

A pass syncs the source (`sync_knowledge_source`), then waits for its interval — or for
`sync_now`, which the console sends. A pass that fails is recorded on the source by the activity
and the loop goes on: a SharePoint briefly unreachable does not end the sync for good. A source
removed ends the loop (`gone`), and so does `stop`. Past a number of passes the loop starts afresh
(`continue_as_new`) so its history stays short.
"""

from __future__ import annotations

import contextlib
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

from .planification import executer_activite

with workflow.unsafe.imports_passed_through():
    from ..activities import knowledge as knowledge_activities

RETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=10))
PASSES_BEFORE_CONTINUE = 100
MIN_INTERVAL_S = 300


@workflow.defn(name="KnowledgeSync", sandboxed=False)
class KnowledgeSync:
    def __init__(self) -> None:
        self.passes = 0
        self.last: dict[str, Any] = {}
        self.stopped = False
        self.wake = False

    @workflow.query
    def status(self) -> dict[str, Any]:
        return {"passes": self.passes, "last": self.last, "stopped": self.stopped}

    @workflow.signal
    def stop(self, payload: dict[str, Any] | None = None) -> None:
        self.stopped = True

    @workflow.signal
    def sync_now(self, payload: dict[str, Any] | None = None) -> None:
        self.wake = True

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        org, source_id = str(payload["org"]), str(payload["source_id"])
        interval = max(MIN_INTERVAL_S, int(payload.get("interval_seconds", 3600)))
        done = 0
        while not self.stopped and done < int(payload.get("max_passes", PASSES_BEFORE_CONTINUE)):
            try:
                self.last = await executer_activite(
                    knowledge_activities.sync_knowledge_source,
                    {"org": org, "source_id": source_id},
                    start_to_close_timeout=timedelta(minutes=30),
                    heartbeat_timeout=None,
                    retry_policy=RETRY,
                )
            except ActivityError as error:
                self.last = {"error": str(error.cause or error)}
            done += 1
            self.passes = int(payload.get("passes", 0)) + done
            if self.last.get("status") == "gone":
                return {"passes": self.passes, "gone": True}
            interval = max(MIN_INTERVAL_S, int(self.last.get("interval_seconds") or interval))
            self.wake = False
            with contextlib.suppress(TimeoutError):
                await workflow.wait_condition(
                    lambda: self.stopped or self.wake, timeout=timedelta(seconds=interval)
                )
        if self.stopped:
            return {"passes": self.passes, "stopped": True}
        workflow.continue_as_new(
            {"org": org, "source_id": source_id, "interval_seconds": interval, "passes": self.passes}
        )
        return None
