# SPDX-License-Identifier: Apache-2.0
"""In-memory executor: plays a scripted `StageResult`, or delegates to a function."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

from choregos_contracts import ExecutorKind, StageInput, StageResult, StageStatus
from choregos_core.domain import ExecRef, ExecStatus, StageJobSpec, utcnow
from choregos_core.hosted import native_sidecars_refusal

from ..errors import UnsupportedClusterError

StageHandler = Callable[[StageInput], StageResult | Any]


class FakeExecutor:
    """Test executor.

    - `queue_result()` queues the results returned, in order;
    - `handler` computes the result from the `StageInput`;
    - `cancel()` marks the run cancelled, like a deleted `PipelineRun`.
    """

    kind = ExecutorKind.FAKE
    capabilities: frozenset[str] = frozenset()

    def __init__(
        self, handler: StageHandler | None = None, capabilities: frozenset[str] = frozenset()
    ) -> None:
        self.handler = handler
        # Scriptable: a test announces `suspend`/`resume` to play an executor that can keep a
        # sandbox across a person's answer (ADR 0016), without one.
        self.capabilities = frozenset(capabilities)
        #: `(run_id, until)` of each suspension; the run ids resumed, in order.
        self.suspended: list[tuple[str, Any]] = []
        self.resumed: list[str] = []
        #: The gateway key each resumption was given, in order (#523).
        self.resumed_keys: list[str | None] = []
        #: The resumed run's sandbox is gone (the bound passed): `resume` answers False.
        self.lost: set[str] = set()
        self.started: list[StageJobSpec] = []
        self.results: dict[str, StageResult] = {}
        self.statuses: dict[str, ExecStatus] = {}
        self.cancelled: list[str] = []
        self._queue: list[StageResult] = []
        self.log_lines: list[str] = ["runner: starting", "runner: result posted"]
        #: Log lines of one run, when scripted (`crash_next`); otherwise `log_lines`.
        self.run_logs: dict[str, list[str]] = {}
        self.start_calls_by_run: dict[str, int] = {}
        self._crashes: list[tuple[ExecStatus, list[str]]] = []
        #: Raised by every `start` while set: an executor that refuses the run (e.g. `ConfigurationError`).
        self.start_error: Exception | None = None
        #: The Kubernetes version the fake plays, as `/version`'s `gitVersion`: a hosted start
        #: below 1.29 is refused as `KubernetesJobExecutor` refuses it (S41-16). `None`: no cluster.
        self.server_version: str | None = None

    def queue_result(self, result: StageResult) -> None:
        self._queue.append(result)

    def crash_next(
        self,
        *,
        exit_code: int | None = 137,
        reason: str = "OOMKilled",
        message: str = "",
        log_lines: list[str] | None = None,
    ) -> None:
        """Script the next started run as a pod that dies without posting a result (S36-02).

        `exit_code=None` with `reason="ImagePullBackOff"` plays a runner that never started.
        The run fails with this status, and `logs()` yields `log_lines` for it.
        """
        status = ExecStatus(
            state="failed",
            exit_code=exit_code,
            reason=reason,
            message=message or f"runner container {reason}",
            started_at=utcnow(),
            ended_at=utcnow(),
        )
        self._crashes.append((status, list(log_lines or [])))

    async def start(self, spec: StageJobSpec) -> ExecRef:
        """Idempotent by `run_id`: two starts of the same run produce one execution."""
        self.start_calls_by_run[spec.run_id] = self.start_calls_by_run.get(spec.run_id, 0) + 1
        if self.start_error is not None:
            raise self.start_error
        if spec.agent_container is not None and self.server_version is not None:
            refusal = native_sidecars_refusal(self.server_version)
            if refusal is not None:
                raise UnsupportedClusterError(refusal)
        if spec.run_id in self.statuses:
            return ExecRef(
                kind=self.kind, name=f"fake-{spec.run_id}", namespace=spec.namespace, run_id=spec.run_id
            )
        self.started.append(spec)
        if self._crashes:
            crashed, lines = self._crashes.pop(0)
            self.statuses[spec.run_id] = crashed
            self.run_logs[spec.run_id] = lines
            return ExecRef(
                kind=self.kind, name=f"fake-{spec.run_id}", namespace=spec.namespace, run_id=spec.run_id
            )
        result: StageResult
        if self._queue:
            result = self._queue.pop(0)
        elif self.handler is not None and spec.stage_input is not None:
            # What a runner sees (#523): its input, with the key its Secret gave it.
            stage_input = spec.stage_input
            if spec.gateway_key and not stage_input.gateway_key:
                stage_input = stage_input.model_copy(update={"gateway_key": spec.gateway_key})
            produced = self.handler(stage_input)
            result = produced if isinstance(produced, StageResult) else StageResult.model_validate(produced)
        else:
            result = _nothing_to_do(spec.stage_input)
        self.results[spec.run_id] = result
        self.statuses[spec.run_id] = ExecStatus(
            state="succeeded",
            exit_code=0,
            reason="Completed",
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
        for line in self.run_logs.get(ref.run_id or "", self.log_lines):
            yield line

    async def suspend(self, ref: ExecRef, until: Any) -> bool:
        run_id = ref.run_id or ""
        if run_id not in self.statuses or run_id in self.lost:
            return False
        self.suspended.append((run_id, until))
        return True

    async def resume(
        self, ref: ExecRef, run_token: str, until: Any, *, gateway_key: str | None = None
    ) -> bool:
        """The same run goes on: its next result is the next one scripted."""
        run_id = ref.run_id or ""
        if run_id in self.lost or not any(r == run_id for r, _ in self.suspended):
            return False
        self.resumed.append(run_id)
        self.resumed_keys.append(gateway_key)
        result = (
            self._queue.pop(0) if self._queue else StageResult(status=StageStatus.DONE, summary="resumed")
        )
        self.results[run_id] = result
        self.statuses[run_id] = ExecStatus(
            state="succeeded",
            exit_code=0,
            result_url=f"fake://runs/{run_id}/result.json",
            started_at=utcnow(),
            ended_at=utcnow(),
        )
        return True

    async def cancel(self, ref: ExecRef) -> None:
        run_id = ref.run_id or ""
        self.cancelled.append(run_id)
        self.lost.add(run_id)  # a cancelled run's sandbox is gone: it cannot be resumed
        self.statuses[run_id] = ExecStatus(state="cancelled", message="cancelled by the orchestrator")

    def result_for(self, run_id: str) -> StageResult | None:
        return self.results.get(run_id)

    async def fetch_result(self, ref: ExecRef) -> StageResult | None:
        """The equivalent of Tekton's `result-url`: the result the runner produced."""
        return self.results.get(ref.run_id or "")


#: Values a typed field of `StageOutputs` accepts, for the outputs a fake step declares it produced.
_PLACEHOLDERS: dict[str, Any] = {"size": "M", "risk": "low", "verdict": "approve", "allowed_paths": ["**"]}


def _nothing_to_do(stage_input: StageInput | None) -> StageResult:
    """The default fake result: done, with every output the transition declares (S31-02) — a
    placeholder value, or an empty file for a typed one. An output an old core template declared
    and nothing produces stays absent, as it always was."""
    from choregos_contracts import ARTIFACT_TYPES, ArtifactFile, Artifacts, artifact_digest
    from choregos_core.dsl.legacy import legacy_outputs

    if stage_input is None:
        return StageResult(status=StageStatus.DONE, summary="fake: nothing to do")
    transition = stage_input.transition
    legacy = set(legacy_outputs(transition.id, list(transition.outputs)))
    files = set(transition.file_outputs())
    outputs = {
        name: _PLACEHOLDERS.get(name, f"fake {name}")
        for name in transition.outputs
        if name not in legacy and name not in files
    }
    stored = {
        name: ArtifactFile(
            filename=f"{name}.{ARTIFACT_TYPES[transition.output_types[name]].extension}",
            type=transition.output_types[name],
            media_type=ARTIFACT_TYPES[transition.output_types[name]].media_type,
            digest=artifact_digest(b""),
            size=0,
        )
        for name in files
    }
    return StageResult(
        status=StageStatus.DONE,
        summary="fake: nothing to do",
        outputs=outputs,
        artifacts=Artifacts(files=stored),
    )


def simulated_stage(stage_input: StageInput) -> StageResult:
    """What a simulated agent hands back: every output the transition declares, and nothing else.

    No model is called. The summary says so, so that nobody mistakes a local install without a
    model key for an agent that worked.
    """
    transition = stage_input.transition
    return StageResult.model_validate(
        {
            "status": StageStatus.DONE,
            "summary": (
                f"simulated agent: `{transition.role}` moved the work item from `{transition.from_}` "
                f"to `{transition.to}` without calling a model (local install in fakes mode)"
            ),
            "outputs": {name: f"(simulated {name}: no model key configured)" for name in transition.outputs},
        }
    )


#: One simulated executor per process. The worker rebuilds a project's adapters for every
#: activity: a fresh `FakeExecutor` would know nothing of the run that `start_run` began, and
#: `await_run` would poll an `unknown` state until the stage timed out.
_SIMULATED: FakeExecutor | None = None


def simulated_executor() -> FakeExecutor:
    """The executor of the `fake` runtime configured with `{"simulate": true}` (local install)."""
    global _SIMULATED  # noqa: PLW0603 - one simulated agent per process, like the other shared fakes
    if _SIMULATED is None:
        _SIMULATED = FakeExecutor(handler=simulated_stage)
    return _SIMULATED
