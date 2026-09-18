"""`FindingsTriage` : un workflow par projet, qui déduplique et crée les tickets liés."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from ..activities import findings as finding_activities

RETRY = RetryPolicy(maximum_attempts=5, initial_interval=timedelta(seconds=2))


@dataclass
class FindingsInput:
    project_slug: str
    pending: list[dict[str, Any]] | None = None


@workflow.defn(name="FindingsTriage", sandboxed=False)
class FindingsTriage:
    def __init__(self) -> None:
        self.queue: deque[dict[str, Any]] = deque()
        self.processed: int = 0
        self.created: list[str] = []
        self.duplicates: int = 0
        self.stopped = False

    @workflow.signal
    def finding(self, payload: dict[str, Any]) -> None:
        self.queue.append(payload)

    @workflow.signal
    def create_ticket(self, payload: dict[str, Any]) -> None:
        """Triage humain : un finding écarté automatiquement peut être forcé en ticket."""
        self.queue.append({**payload, "forced": True})

    @workflow.signal
    def stop(self, payload: dict[str, Any]) -> None:
        self.stopped = True

    @workflow.query
    def status(self) -> dict[str, Any]:
        return {
            "queued": len(self.queue),
            "processed": self.processed,
            "created": list(self.created),
            "duplicates": self.duplicates,
        }

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        params = FindingsInput(**payload)
        self.queue.extend(params.pending or [])
        while not self.stopped and self.processed < 500:
            await workflow.wait_condition(lambda: bool(self.queue) or self.stopped)
            while self.queue:
                item = self.queue.popleft()
                outcome = await workflow.execute_activity(
                    finding_activities.triage_finding,
                    {"project_slug": params.project_slug, "finding_id": item["finding_id"]},
                    start_to_close_timeout=timedelta(minutes=3),
                    retry_policy=RETRY,
                )
                self.processed += 1
                if outcome.get("status") == "created":
                    self.created.append(outcome.get("work_item_key", ""))
                    if outcome.get("agent_ready"):
                        safe_key = outcome["work_item_key"].replace("/", "_").replace("#", "-")
                        await workflow.start_child_workflow(
                            "WorkflowInterpreter",
                            {
                                "project_id": item.get("project_id", params.project_slug),
                                "project_slug": params.project_slug,
                                "work_item_id": outcome["work_item_id"],
                                "tracker_key": outcome["work_item_key"],
                            },
                            id=f"wi-{params.project_slug}-{safe_key}",
                            parent_close_policy=workflow.ParentClosePolicy.ABANDON,
                        )
                elif outcome.get("status") == "duplicate":
                    self.duplicates += 1
            if workflow.info().get_current_history_length() > 10_000:
                workflow.continue_as_new({"project_slug": params.project_slug, "pending": list(self.queue)})
        return {"processed": self.processed, "created": self.created, "duplicates": self.duplicates}
