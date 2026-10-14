# SPDX-License-Identifier: Apache-2.0
"""`EvalMatrix`: nightly evals, backend × model × memory (docs/plan/04 §4.2, S12-02).

S37-09 adds the context reduction as a fourth dimension (ADR 0055). Its default, `["none"]`, keeps
the commands of a matrix started before it: one cell per backend × model × memory, in the same
order, so its history replays (`tests/replay/histories/evals-before-s37-09.json`).

S37-10: `live: true` plays each case as a real stage on the eval project's executor instead of the
fixture's scripted change. Its default, `false`, keeps every command of the scripted matrix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

from .planification import executer_activite

with workflow.unsafe.imports_passed_through():
    from ..activities import evals as eval_activities
    from ..activities import stage as stage_activities
    from ..activities.interpretation import message_de

RETRY = RetryPolicy(maximum_attempts=2, initial_interval=timedelta(seconds=5))
#: The interpreter's, for the same activities (`workflows/interpreter.py`).
STAGE_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=5,
)
POLL_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=20,
)


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
    # S37-10: real runs on the project's executor, judged by the facts the runner measured.
    live: bool = False


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
                        if params.live:
                            cell = {
                                "project_slug": params.project_slug,
                                "backend": backend,
                                "model": model,
                                "with_memory": memory,
                                "context": context,
                            }
                            self.results.append(await self._live_cell(cell, fixtures))
                            continue
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

    async def _live_cell(self, cell: dict[str, Any], fixtures: list[str]) -> dict[str, Any]:
        outcomes = [await self._live_case(cell, fixture) for fixture in fixtures]
        result: dict[str, Any] = await executer_activite(
            eval_activities.summarize_eval_cell,
            {**cell, "outcomes": outcomes},
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=RETRY,
        )
        return result

    async def _live_case(self, cell: dict[str, Any], fixture: str) -> dict[str, Any]:
        """One case as the interpreter runs a stage, then judged. A run that fails is a case that
        failed, never a matrix that stops: the next cases still run."""
        seeded = await executer_activite(
            eval_activities.seed_eval_ticket,
            {**cell, "fixture": fixture, "matrix_id": workflow.info().workflow_id},
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RETRY,
        )
        if seeded["skipped"]:
            return {**seeded, "fixture": fixture}
        plan = seeded["plan"]
        try:
            prepared = await executer_activite(
                stage_activities.prepare_stage,
                plan,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=STAGE_RETRY,
            )
        except ActivityError as exc:
            # No run was made: the platform could not prepare it, the agent never had the case.
            reason = f"the stage could not be prepared: {message_de(exc)}"
            return {
                "ok": False,
                "skipped": True,
                "reason": reason,
                "usage": None,
                "duration_s": 0.0,
                "fixture": fixture,
            }
        run_id = prepared["run_id"]
        minutes = int(prepared["stage_input"]["budget"]["max_minutes"])
        ids = {"project_id": plan["project_id"], "run_id": run_id}
        failure = ""
        try:
            await executer_activite(
                stage_activities.start_run,
                {
                    "project_id": plan["project_id"],
                    "work_item_id": plan["work_item_id"],
                    "stage_input": prepared["stage_input"],
                },
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=STAGE_RETRY,
            )
            awaited = await executer_activite(
                stage_activities.await_run,
                {**ids, "timeout_minutes": minutes + 10},
                start_to_close_timeout=timedelta(minutes=minutes + 20),
                schedule_to_close_timeout=timedelta(minutes=minutes + 90),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=POLL_RETRY,
            )
            await executer_activite(
                stage_activities.record_run_outcome,
                {**ids, "result": awaited["result"]},
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=STAGE_RETRY,
            )
        except ActivityError as exc:
            failure = message_de(exc)
        # The key is revoked and the ledger written whatever the run did: a failed case costs too.
        await executer_activite(
            stage_activities.collect_spend,
            ids,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=STAGE_RETRY,
        )
        verdict: dict[str, Any] = await executer_activite(
            eval_activities.judge_eval_run,
            {**ids, "fixture": fixture, "context": cell["context"], "failure": failure},
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=RETRY,
        )
        return verdict
