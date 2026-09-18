"""`MemoryIngestion` : import planifié tracker/SCM/CD → mémoire (docs/plan/04)."""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from ..activities import memory as memory_activities

RETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=5))
DEFAULT_SOURCES = ["closed_issues", "merged_prs", "adr", "docs", "deployments", "run_lessons"]


@dataclass
class MemoryInput:
    project_slug: str
    interval_minutes: int = 15
    sources: list[str] | None = None
    max_cycles: int = 96


@workflow.defn(name="MemoryIngestion", sandboxed=False)
class MemoryIngestion:
    def __init__(self) -> None:
        self.events: deque[dict[str, Any]] = deque()
        self.requested: list[str] | None = None
        self.cycles = 0
        self.ingested = 0
        self.stopped = False

    @workflow.signal
    def reimport(self, payload: dict[str, Any]) -> None:
        self.requested = list(payload.get("sources") or DEFAULT_SOURCES)

    @workflow.signal
    def alert(self, payload: dict[str, Any]) -> None:
        """Une alerte devient un incident en mémoire, sans attendre le prochain cycle."""
        self.events.append(payload)

    @workflow.signal
    def stop(self, payload: dict[str, Any]) -> None:
        self.stopped = True

    @workflow.query
    def status(self) -> dict[str, Any]:
        return {"cycles": self.cycles, "ingested": self.ingested, "queued": len(self.events)}

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        params = MemoryInput(**payload)
        sources = params.sources or DEFAULT_SOURCES
        while self.cycles < params.max_cycles and not self.stopped:
            with contextlib.suppress(asyncio.TimeoutError):
                await workflow.wait_condition(
                    lambda: bool(self.events) or self.requested is not None or self.stopped,
                    timeout=timedelta(minutes=params.interval_minutes),
                )
            while self.events:
                event = self.events.popleft()
                await workflow.execute_activity(
                    memory_activities.ingest_alert,
                    {"project_slug": params.project_slug, "event": event},
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=RETRY,
                )
            wanted = self.requested or sources
            self.requested = None
            outcome = await workflow.execute_activity(
                memory_activities.ingest_sources,
                {"project_slug": params.project_slug, "sources": wanted},
                start_to_close_timeout=timedelta(minutes=15),
                retry_policy=RETRY,
            )
            self.ingested += int(outcome.get("facts", 0))
            self.cycles += 1
            if workflow.info().get_current_history_length() > 10_000:
                workflow.continue_as_new(payload)
        return {"cycles": self.cycles, "ingested": self.ingested}
