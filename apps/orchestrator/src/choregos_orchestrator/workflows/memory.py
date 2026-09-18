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
    org: str = ""
    ab_report_weeks: int = 4
    last_report_week: int = 0


@workflow.defn(name="MemoryIngestion", sandboxed=False)
class MemoryIngestion:
    def __init__(self) -> None:
        self.events: deque[dict[str, Any]] = deque()
        self.requested: list[str] | None = None
        self.cycles = 0
        self.ingested = 0
        self.stopped = False
        self.last_report_week = 0
        self.last_report: dict[str, Any] = {}

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

    @workflow.signal
    def ab_report_now(self, payload: dict[str, Any] | None = None) -> None:
        """Force le rapport A/B au prochain cycle, sans attendre le changement de semaine."""
        self.last_report_week = 0

    @workflow.query
    def status(self) -> dict[str, Any]:
        return {
            "cycles": self.cycles,
            "ingested": self.ingested,
            "queued": len(self.events),
            "last_report": self.last_report,
        }

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
            await self._weekly_ab_report(params)
            if workflow.info().get_current_history_length() > 10_000:
                workflow.continue_as_new({**payload, "last_report_week": self.last_report_week})
        return {"cycles": self.cycles, "ingested": self.ingested}

    async def _weekly_ab_report(self, params: MemoryInput) -> None:
        """Une fois par semaine ISO : la mémoire paie-t-elle ? (docs/plan/04, décision 4)

        `workflow.now()` est déterministe : le rapport part une seule fois par semaine,
        même si le workflow est rejoué ou reprend après un `continue_as_new`.
        """
        if not params.org:
            return
        week = workflow.now().isocalendar().week
        if week == (self.last_report_week or params.last_report_week):
            return
        self.last_report = await workflow.execute_activity(
            memory_activities.memory_ab_report,
            {"org": params.org, "weeks": params.ab_report_weeks, "notify": True},
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RETRY,
        )
        self.last_report_week = week
