"""`EvalMatrix` : évals nocturnes backend × modèle × mémoire (docs/plan/04 §4.2, S12-02)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from ..activities import evals as eval_activities

RETRY = RetryPolicy(maximum_attempts=2, initial_interval=timedelta(seconds=5))


@dataclass
class EvalInput:
    project_slug: str
    backends: list[str] = field(default_factory=lambda: ["openhands"])
    models: list[str] = field(default_factory=lambda: ["platform/standard"])
    with_memory: list[bool] = field(default_factory=lambda: [True, False])
    fixtures: list[str] = field(default_factory=list)
    publish: bool = True


@workflow.defn(name="EvalMatrix", sandboxed=False)
class EvalMatrix:
    def __init__(self) -> None:
        self.results: list[dict[str, Any]] = []
        self.done = False

    @workflow.query
    def status(self) -> dict[str, Any]:
        return {"results": len(self.results), "done": self.done}

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        params = EvalInput(**payload)
        fixtures = params.fixtures or await workflow.execute_activity(
            eval_activities.list_fixtures,
            {},
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=RETRY,
        )
        for backend in params.backends:
            for model in params.models:
                for memory in params.with_memory:
                    outcome = await workflow.execute_activity(
                        eval_activities.run_eval_cell,
                        {
                            "project_slug": params.project_slug,
                            "backend": backend,
                            "model": model,
                            "with_memory": memory,
                            "fixtures": fixtures,
                        },
                        start_to_close_timeout=timedelta(hours=2),
                        heartbeat_timeout=timedelta(minutes=10),
                        retry_policy=RETRY,
                    )
                    self.results.append(outcome)
        if params.publish:
            await workflow.execute_activity(
                eval_activities.publish_matrix,
                {"project_slug": params.project_slug, "results": self.results},
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=RETRY,
            )
        self.done = True
        return {"cells": len(self.results), "results": self.results}
