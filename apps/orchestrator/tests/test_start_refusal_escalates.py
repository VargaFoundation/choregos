"""A run the executor refuses on the configuration escalates the work item (S38-13e, #750).

On the dev tenant (2026-10-19), `preset:regulated` asked for gVisor and the warm pool ran the
cluster default. Since S38-13b `start_run` closes the run `failed` with the reason and raises a
non-retryable `executor_configuration_refused`; the interpreter then failed, and the work item had
no workflow. It now parks where a failed attempt would, with the refusal as the reason, and
`rerun_stage` replays the stage once the deployment is fixed.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
from choregos_adapters.errors import ConfigurationError
from choregos_adapters.fakes.executor import FakeExecutor

from .conftest import Fixture, recording_history, refine_result
from .kits.escalation import requests_of
from .test_interpreter import _runs, _wait_state, _wait_until, scripted, start
from .test_restarted_interpreter_resumes import _state_changes

pytestmark = pytest.mark.asyncio

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"
REASON = (
    "the run asks for RuntimeClass `gvisor`, the warm pool `choregos-runner` runs `the cluster default`: "
    "set `runner.runtimeClass` to match the policy"
)


def _refusing(setup: Fixture) -> FakeExecutor:
    executor = setup.adapters.executor
    assert isinstance(executor, FakeExecutor)
    executor.start_error = ConfigurationError(REASON)
    return executor


async def test_a_refused_start_escalates_and_the_stage_replays_once_fixed(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from sqlalchemy import select
    from temporalio.worker import Replayer

    executor = _refusing(setup)
    scripted(setup.adapters, refine_result("spec"))
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "needs_human")
        await _wait_until(handle, "the work item waits for a person", lambda s: s["pending_request"])
        description = await handle.describe()
        assert description.status is not None and description.status.name == "RUNNING", "not failed"
        assert sum(executor.start_calls_by_run.values()) == 1, "escalated at once, not retried"

        # The reason is where a person looks: the request, the state change, the failed run.
        asked = [d for d in await requests_of(setup) if (d.payload or {}).get("reason") == "configuration"]
        assert asked and "RuntimeClass `gvisor`" in asked[0].payload["question"], asked
        arrival = [e.payload for e in await _state_changes(setup) if e.payload.get("to") == "needs_human"][-1]
        assert "the executor refused the run" in arrival["reason"], arrival
        assert arrival["parked"] == ["needs_human", arrival["from"], "t-refine"], arrival
        async with session_scope() as session:
            runs = list((await session.execute(select(Run))).scalars())
        assert [r.status for r in runs] == ["failed"], [r.status for r in runs]

        executor.start_error = None  # the operator fixes the deployment
        await handle.signal("control", {"action": "rerun_stage", "by": "operator"})
        await _wait_state(handle, "awaiting_spec_approval")
        assert await _runs(setup) == 2
        await handle.signal("control", {"action": "stop"})
        await handle.result()
        history = await handle.fetch_history()

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        target = HISTORIES / "wi-start-refusal-S38-13e.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_a_history_before_the_marker_still_fails(
    setup: Fixture, temporal_env: Any, worker_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run recorded before the marker answers no to `patched`: the refusal fails it, as then."""
    from choregos_orchestrator.workflows import interpreter
    from temporalio.client import WorkflowFailureError

    real = interpreter.workflow.patched
    monkeypatch.setattr(
        interpreter.workflow,
        "patched",
        lambda patch_id: False if patch_id == interpreter.START_REFUSAL_ESCALATES else real(patch_id),
    )
    _refusing(setup)
    async with worker_factory():
        handle = await start(temporal_env, setup)
        with pytest.raises(WorkflowFailureError):
            await handle.result()


async def test_another_start_error_still_fails_the_interpreter(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """Only the executor's configuration refusal escalates: a bug in `start` still fails the
    interpreter once its retries are spent, as before."""
    from temporalio.client import WorkflowFailureError
    from temporalio.exceptions import ApplicationError

    executor = setup.adapters.executor
    assert isinstance(executor, FakeExecutor)
    executor.start_error = ApplicationError("a genuine bug", type="KeyError", non_retryable=True)
    async with worker_factory():
        handle = await start(temporal_env, setup)
        with pytest.raises(WorkflowFailureError):
            await handle.result()
