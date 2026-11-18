"""A decision names the request it answers, and only the pending request takes it (#1795, S67-15).

An approval made for a request of `pr_open`, delivered once the work item had escalated to
`needs_human`, was applied to the escalation (`needs_human -> abandoned`, "human decision: approved"):
a person's click for one thing decided another. A decision whose `request_id` is not the pending
request is now journaled and ignored; a decision without `request_id` (an old sender) is applied as
before.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
from tranibor_contracts import StageResult, StageStatus

from .conftest import Fixture, recording_history
from .kits.escalation import requests_of
from .test_interpreter import _ticket, _wait_state, _wait_until, scripted, start

pytestmark = pytest.mark.asyncio

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"
FAILURE = StageResult(status=StageStatus.FAILED, summary="the clone failed", reason="agent_error")
APPROVED = {"approved": True, "decided_by": "marie", "kind": "approval"}


async def _escalated(setup: Fixture, temporal_env: Any) -> tuple[Any, str]:
    """The work item, escalated to `needs_human` with the abandon offered: its handle, the request."""
    scripted(setup.adapters, FAILURE, FAILURE)
    handle = await start(temporal_env, setup)
    await _wait_state(handle, "needs_human")
    await _wait_until(handle, "abandon is offered", lambda s: s["pending_request"])
    (abandon,) = [r for r in await requests_of(setup) if r.transition_id == "t-abandon"]
    return handle, abandon.id


async def test_a_decision_for_another_request_is_ignored_in_the_escalation(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """The stale approval does not abandon the escalated work item: it is journaled, and the person's
    decision on the abandon request still does."""
    from temporalio.worker import Replayer
    from tranibor_orchestrator.workflows import ALL_WORKFLOWS

    async with worker_factory():
        handle, abandon_id = await _escalated(setup, temporal_env)
        await handle.signal("human_decision", {**APPROVED, "request_id": "request-of-pr-open"})
        await _wait_until(
            handle,
            "the stale decision is journaled",
            lambda s: s["stale_decisions"] == ["request-of-pr-open"],
        )
        status = await handle.query("status")
        assert status["state"] == "needs_human", status
        assert status["pending_request"] == abandon_id, "the abandon is still offered"
        assert (await _ticket(setup)).state == "needs_human"

        await handle.signal("human_decision", {**APPROVED, "request_id": abandon_id})
        await _wait_state(handle, "abandoned")
        await handle.result()
        history = await handle.fetch_history()
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        target = HISTORIES / "wi-decision-names-its-request-S67-15.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_a_decision_without_a_request_is_applied_as_before(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """An old sender names no request: its decision decides what waits, and nothing is journaled."""
    async with worker_factory():
        handle, _ = await _escalated(setup, temporal_env)
        await handle.signal("human_decision", APPROVED)
        await _wait_state(handle, "abandoned")
        status = await handle.query("status")
        await handle.result()
    assert status["stale_decisions"] == []


async def test_a_decision_for_the_pending_request_is_applied(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    async with worker_factory():
        handle, abandon_id = await _escalated(setup, temporal_env)
        await handle.signal("human_decision", {**APPROVED, "request_id": abandon_id})
        await _wait_state(handle, "abandoned")
        status = await handle.query("status")
        await handle.result()
    assert status["stale_decisions"] == []
