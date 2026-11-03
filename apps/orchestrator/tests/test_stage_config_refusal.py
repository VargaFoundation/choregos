"""A stage refused on its configuration escalates the work item instead of killing it (#543).

On the dev tenant (2026-10-15, v0.17.14), `prepare_stage` raised `ModelResolutionError` — "the model
`platform/standard` does not support tool calling". The failure reached `record_workflow_failure`,
then WORKFLOW_EXECUTION_FAILED: the interpreters of sandbox-dev#4 and #6 died, and their work items
stayed `live` with no workflow. A configuration an operator can fix now parks the work item where a
failed attempt would, with the refusal as the reason, and `rerun_stage` replays the stage once the
configuration is fixed.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from .conftest import Fixture, recording_history, refine_result
from .kits.escalation import requests_of
from .test_interpreter import _runs, _wait_state, _wait_until, scripted, start
from .test_restarted_interpreter_resumes import _state_changes

pytestmark = pytest.mark.asyncio

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"


def _without_tool_calling(gateway: Any) -> list[Any]:
    """The gateway's catalogue, with every model losing tool calling — what the dev tenant's
    gateway said of `platform/standard`. Returns the catalogue as it was, to put back."""
    before = list(gateway.models)
    gateway.models = [m.model_copy(update={"supports_tool_calling": False}) for m in before]
    return before


async def _refused_then_parked(temporal_env: Any, setup: Fixture) -> Any:
    handle = await start(temporal_env, setup)
    await _wait_state(handle, "needs_human")
    await _wait_until(handle, "the work item waits for a person", lambda s: s["pending_request"])
    assert await _runs(setup) == 0, "the refusal comes before any run"
    description = await handle.describe()
    assert description.status is not None and description.status.name == "RUNNING"
    return handle


async def test_a_refused_model_escalates_and_the_stage_replays_once_fixed(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    catalogue = _without_tool_calling(setup.adapters.gateway)
    scripted(setup.adapters, refine_result("spec"))
    async with worker_factory():
        handle = await _refused_then_parked(temporal_env, setup)

        # The reason is where a person looks: the request, and the state change.
        asked = [d for d in await requests_of(setup) if (d.payload or {}).get("reason") == "configuration"]
        assert asked and "does not support tool calling" in asked[0].payload["question"], asked
        arrival = [e.payload for e in await _state_changes(setup) if e.payload.get("to") == "needs_human"][-1]
        assert "does not support tool calling" in arrival["reason"], arrival
        # Where it resumes is stored: a restarted interpreter can replay it too (S22-41).
        assert arrival["parked"] == ["needs_human", arrival["from"], "t-refine"], arrival

        setup.adapters.gateway.models = catalogue  # the operator fixes the configuration
        await handle.signal("control", {"action": "rerun_stage", "by": "operator"})
        await _wait_state(handle, "awaiting_spec_approval")
        assert await _runs(setup) == 1
        await handle.signal("control", {"action": "stop"})
        await handle.result()
        history = await handle.fetch_history()

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        target = HISTORIES / "wi-stage-config-refusal-S22-43.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_a_restarted_interpreter_replays_the_refused_stage(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """The escalation context is stored: an interpreter started again from the database (#497)
    replays the refused stage too."""
    catalogue = _without_tool_calling(setup.adapters.gateway)
    scripted(setup.adapters, refine_result("spec"))
    async with worker_factory():
        first = await _refused_then_parked(temporal_env, setup)
        await first.terminate(reason="the interpreter died (#497)")
        handle = await start(temporal_env, setup)
        await _wait_until(handle, "the restarted interpreter waits", lambda s: s["pending_request"])

        setup.adapters.gateway.models = catalogue
        await handle.signal("control", {"action": "rerun_stage", "by": "operator"})
        await _wait_state(handle, "awaiting_spec_approval")
        assert await _runs(setup) == 1
        await handle.signal("control", {"action": "stop"})
        await handle.result()


async def test_another_non_retryable_error_still_fails_the_interpreter(
    setup: Fixture, temporal_env: Any, worker_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only configuration refusals escalate: any other error in `prepare_stage` — here a bug that
    says it should not be retried — fails the interpreter, and says so, as before."""
    from choregos_orchestrator.activities import stage
    from temporalio.client import WorkflowFailureError
    from temporalio.exceptions import ApplicationError

    def bug(*_: Any) -> Any:
        raise ApplicationError("a genuine bug", type="KeyError", non_retryable=True)

    monkeypatch.setattr(stage, "_resolve_model", bug)
    async with worker_factory():
        handle = await start(temporal_env, setup)
        with pytest.raises(WorkflowFailureError):
            await handle.result()
    assert await _runs(setup) == 0


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
        lambda patch_id: False if patch_id == interpreter.STAGE_CONFIG_REFUSAL_ESCALATES else real(patch_id),
    )
    _without_tool_calling(setup.adapters.gateway)
    async with worker_factory():
        handle = await start(temporal_env, setup)
        with pytest.raises(WorkflowFailureError):
            await handle.result()
