"""A status comment the tracker does not take no longer stops the work item (#1646, S62-05).

In production (project `iris`, FON-277, 2026-11-09) a work item stood still for two days after a
refused result; S62-05's row traced it to the status comment's activity timing out. `_mirror` awaited
`update_status_comment` and nothing caught its last `ActivityError`, so a slow tracker failed the
interpreter for a comment that only mirrors the platform's state. The comment is now best-effort,
behind `STATUS_COMMENT_BEST_EFFORT`; `mirror_state` (the state itself, in the database) stays blocking.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
from choregos_adapters.fakes.tracker import FakeTracker

from .conftest import Fixture, recording_history, refine_result
from .test_interpreter import _wait_state, scripted, start

pytestmark = pytest.mark.asyncio

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"


def _comment_refused(setup: Fixture, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The tracker refuses every status comment, as an unreachable Jira would; returns the calls."""
    tracker = setup.adapters.tracker
    assert isinstance(tracker, FakeTracker)
    calls: list[str] = []

    async def refused(key: str, markdown: str, marker: str) -> None:
        calls.append(key)
        raise RuntimeError("the tracker did not answer in time")

    monkeypatch.setattr(tracker, "upsert_status_comment", refused)
    return calls


async def test_a_refused_status_comment_does_not_stop_the_work_item(
    setup: Fixture, temporal_env: Any, worker_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    calls = _comment_refused(setup, monkeypatch)
    scripted(setup.adapters, refine_result("spec"))
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "awaiting_spec_approval")
        description = await handle.describe()
        assert description.status is not None and description.status.name == "RUNNING", "not failed"
        # Reaching this state took the first mirror past its comment: its five attempts all refused.
        assert len(calls) >= 5, f"the first comment spent its retries: {len(calls)} calls"
        async with session_scope() as session:
            item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None and item.state == "awaiting_spec_approval", "the state is still written"
        await handle.signal("control", {"action": "stop"})
        await handle.result()
        history = await handle.fetch_history()

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        target = HISTORIES / "wi-status-comment-best-effort-S62-05.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_a_history_before_the_marker_still_fails(
    setup: Fixture, temporal_env: Any, worker_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run recorded before the marker answers no to `patched`: the refused comment fails it, as then."""
    from choregos_orchestrator.workflows import interpreter
    from temporalio.client import WorkflowFailureError

    real = interpreter.workflow.patched
    monkeypatch.setattr(
        interpreter.workflow,
        "patched",
        lambda patch_id: False if patch_id == interpreter.STATUS_COMMENT_BEST_EFFORT else real(patch_id),
    )
    _comment_refused(setup, monkeypatch)
    scripted(setup.adapters, refine_result("spec"))
    async with worker_factory():
        handle = await start(temporal_env, setup)
        with pytest.raises(WorkflowFailureError):
            await handle.result()


async def test_a_refused_state_still_fails_the_interpreter(
    setup: Fixture, temporal_env: Any, worker_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the comment is best-effort: a board the tracker refuses rolls the state back with it, and
    the interpreter fails as before rather than go on from a state the platform did not record."""
    from temporalio.client import WorkflowFailureError

    tracker = setup.adapters.tracker
    assert isinstance(tracker, FakeTracker)

    async def refused(key: str, state_mapping: Any) -> None:
        raise RuntimeError("the tracker did not answer in time")

    monkeypatch.setattr(tracker, "set_state", refused)
    async with worker_factory():
        handle = await start(temporal_env, setup)
        with pytest.raises(WorkflowFailureError):
            await handle.result()
