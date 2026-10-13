# SPDX-License-Identifier: Apache-2.0
"""`EvalMatrix`: nightly evals, backend × model × memory (docs/plan/04 §4.2, S12-02).

S37-09 adds the context reduction as a fourth dimension (ADR 0055). Its default, `["none"]`, keeps
the commands of a matrix started before it: one cell per backend × model × memory, in the same
order, so its history replays (`tests/replay/histories/evals-before-s37-09.json`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

from .planification import executer_activite

with workflow.unsafe.imports_passed_through():
    from ..activities import evals as eval_activities

RETRY = RetryPolicy(maximum_attempts=2, initial_interval=timedelta(seconds=5))


@dataclass
class EvalInput:
    project_slug: str
    backends: list[str] = field(default_factory=lambda: ["claude-code"])
    models: list[str] = field(default_factory=lambda: ["platform/standard"])
    with_memory: list[bool] = field(default_factory=lambda: [True, False])
    fixtures: list[str] = field(default_factory=list)
    publish: bool = True
    # none | rtk | headroom | rtk+headroom (ADR 0055): what the cell's runs are given as context
    # reduction. Checked by `run_eval_cell`, not here: a workflow that raises on its input fails
    # its task forever instead of failing once.
    contexts: list[str] = field(default_factory=lambda: ["none"])


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
        fixtures = params.fixtures or await executer_activite(
            eval_activities.list_fixtures,
            {},
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=RETRY,
        )
        for backend in params.backends:
            for model in params.models:
                for memory in params.with_memory:
                    for context in params.contexts:
                        outcome = await executer_activite(
                            eval_activities.run_eval_cell,
                            {
                                "project_slug": params.project_slug,
                                "backend": backend,
                                "model": model,
                                "with_memory": memory,
                                "context": context,
                                "fixtures": fixtures,
                            },
                            start_to_close_timeout=timedelta(hours=2),
                            heartbeat_timeout=timedelta(minutes=10),
                            retry_policy=RETRY,
                        )
                        self.results.append(outcome)
        if params.publish:
            await executer_activite(
                eval_activities.publish_matrix,
                {"project_slug": params.project_slug, "results": self.results},
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=RETRY,
            )
        self.done = True
        return {"cells": len(self.results), "results": self.results}
