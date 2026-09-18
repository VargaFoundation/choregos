"""Exécuteur en mémoire : joue un `StageResult` scripté, ou délègue à une fonction."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

from choregos_contracts import ExecutorKind, StageInput, StageResult, StageStatus
from choregos_core.domain import ExecRef, ExecStatus, StageJobSpec, utcnow

StageHandler = Callable[[StageInput], StageResult | Any]


class FakeExecutor:
    """Exécuteur de test.

    - `queue_result()` empile les résultats rendus dans l'ordre ;
    - `handler` permet de calculer le résultat à partir du `StageInput` ;
    - `cancel()` marque le run annulé, comme un `PipelineRun` supprimé.
    """

    kind = ExecutorKind.FAKE

    def __init__(self, handler: StageHandler | None = None) -> None:
        self.handler = handler
        self.started: list[StageJobSpec] = []
        self.results: dict[str, StageResult] = {}
        self.statuses: dict[str, ExecStatus] = {}
        self.cancelled: list[str] = []
        self._queue: list[StageResult] = []
        self.log_lines: list[str] = ["runner: démarrage", "runner: résultat posté"]
        self.start_calls_by_run: dict[str, int] = {}

    def queue_result(self, result: StageResult) -> None:
        self._queue.append(result)

    async def start(self, spec: StageJobSpec) -> ExecRef:
        """Idempotent par `run_id` : deux démarrages du même run ne produisent qu'une exécution."""
        self.start_calls_by_run[spec.run_id] = self.start_calls_by_run.get(spec.run_id, 0) + 1
        if spec.run_id in self.statuses:
            return ExecRef(
                kind=self.kind, name=f"fake-{spec.run_id}", namespace=spec.namespace, run_id=spec.run_id
            )
        self.started.append(spec)
        result: StageResult
        if self._queue:
            result = self._queue.pop(0)
        elif self.handler is not None and spec.stage_input is not None:
            produced = self.handler(spec.stage_input)
            result = produced if isinstance(produced, StageResult) else StageResult.model_validate(produced)
        else:
            result = StageResult(status=StageStatus.DONE, summary="fake: rien à faire")
        self.results[spec.run_id] = result
        self.statuses[spec.run_id] = ExecStatus(
            state="succeeded",
            exit_code=0,
            result_url=f"fake://runs/{spec.run_id}/result.json",
            started_at=utcnow(),
            ended_at=utcnow(),
        )
        return ExecRef(
            kind=self.kind, name=f"fake-{spec.run_id}", namespace=spec.namespace, run_id=spec.run_id
        )

    async def status(self, ref: ExecRef) -> ExecStatus:
        await asyncio.sleep(0)
        return self.statuses.get(ref.run_id or "", ExecStatus(state="unknown"))

    async def logs(self, ref: ExecRef) -> AsyncIterator[str]:
        for line in self.log_lines:
            yield line

    async def cancel(self, ref: ExecRef) -> None:
        run_id = ref.run_id or ""
        self.cancelled.append(run_id)
        self.statuses[run_id] = ExecStatus(state="cancelled", message="annulé par l'orchestrateur")

    def result_for(self, run_id: str) -> StageResult | None:
        return self.results.get(run_id)

    async def fetch_result(self, ref: ExecRef) -> StageResult | None:
        """Équivalent du `result-url` Tekton : le résultat produit par le runner."""
        return self.results.get(ref.run_id or "")
