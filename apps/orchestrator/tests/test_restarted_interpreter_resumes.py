"""A restarted interpreter can replay the stage that escalated (S22-41).

On the dev tenant (2026-10-15, v0.17.13), two work items escalated to `needs_human` after
`t-verify-prod` failed. Their interpreters died (#497) and were started again from the database:
`load_context` gave the state, nothing gave the stage that escalated. `rerun_stage` was received
and dropped — the only way out was abandoning work already in production.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
from typing import Any

import pytest
from choregos_contracts import StageResult, StageStatus

from .conftest import Fixture, refine_result
from .test_interpreter import _runs, _wait_state, _wait_until, scripted, start

pytestmark = pytest.mark.asyncio

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"
FAILURE = StageResult(status=StageStatus.FAILED, summary="the clone failed", reason="agent_error")


async def _state_changes(setup: Fixture) -> list[Any]:
    from choregos_api.db.models import Event
    from choregos_api.db.session import session_scope
    from choregos_contracts import EventType
    from sqlalchemy import select

    async with session_scope() as session:
        rows = await session.execute(
            select(Event)
            .where(
                Event.work_item_id == setup.work_item_id,
                Event.type == str(EventType.WORKITEM_STATE_CHANGED),
            )
            .order_by(Event.ts)
        )
        return list(rows.scalars())


async def _forget_the_parking(setup: Fixture) -> None:
    """What an escalation recorded before S22-41 left: the state change, without `parked`."""
    from choregos_api.db.models import Event
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        for event in await _state_changes(setup):
            row = await session.get(Event, event.id)
            assert row is not None
            row.payload = {k: v for k, v in (row.payload or {}).items() if k != "parked"}


async def _wait_mirrored(setup: Fixture, state: str, *, timeout: int = 60) -> None:
    """Waits until the DATABASE holds `state` and has applied the runs' decisions.

    `status` answers from the interpreter's memory, which moves before `mirror_state` commits. An
    interpreter terminated in that window leaves the database half-way: a restart reads the state
    (`load_context`) before the commit and the attempts after it, and plays a second run (#748).
    A test that kills an interpreter waits for the commit, not for the query.
    """
    import time

    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from sqlalchemy import func, select

    deadline = time.monotonic() + timeout
    seen: tuple[str | None, int] = (None, -1)
    while time.monotonic() < deadline:
        async with session_scope() as session:
            item = await session.get(WorkItem, setup.work_item_id)
            undecided = await session.scalar(
                select(func.count())
                .select_from(Run)
                .where(
                    Run.work_item_id == setup.work_item_id,
                    Run.status == "succeeded",
                    Run.decided_at.is_(None),
                )
            )
        seen = (item.state if item else None, int(undecided or 0))
        if seen == (state, 0):
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"`{state}` never committed with every decision applied (last: {seen})")


async def _escalate_then_restart(temporal_env: Any, setup: Fixture, *, legacy: bool = False) -> Any:
    """Two failures escalate `t-refine`; the interpreter dies; a new one starts from the database."""
    first = await start(temporal_env, setup)
    await _wait_state(first, "needs_human")
    await _wait_until(first, "the abandon is proposed", lambda s: s["pending_request"])
    assert await _runs(setup) == 2
    await first.terminate(reason="the interpreter died (#497)")
    if legacy:
        await _forget_the_parking(setup)
    restarted = await start(temporal_env, setup)
    await _wait_until(restarted, "the restarted interpreter waits", lambda s: s["pending_request"])
    status = await restarted.query("status")
    assert status["state"] == "needs_human" and status["attempts"] == {"t-refine": 2}, status
    return restarted


async def test_the_escalation_records_where_the_work_item_resumes(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    scripted(setup.adapters, FAILURE, FAILURE)
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "needs_human")
        await handle.signal("control", {"action": "stop"})
        await handle.result()
        history = await handle.fetch_history()

    arrival = [e.payload for e in await _state_changes(setup) if e.payload.get("to") == "needs_human"][-1]
    assert arrival["parked"] == ["needs_human", arrival["from"], "t-refine"], arrival
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if os.environ.get("CHOREGOS_ARCHIVER_HISTORIQUE"):
        target = HISTORIES / "wi-escalation-stored-S22-41.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_a_restarted_interpreter_replays_the_escalated_stage(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """`rerun_stage` on a restarted interpreter replays `t-refine` with a NEW run number — attempt
    1 again would name the failed run, and `prepare_stage` would hand it back — and reaches its
    target."""
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    scripted(setup.adapters, FAILURE, FAILURE, refine_result("spec"))
    async with worker_factory():
        handle = await _escalate_then_restart(temporal_env, setup)
        await handle.signal("control", {"action": "rerun_stage", "by": "operator"})
        await _wait_state(handle, "awaiting_spec_approval")
        status = await handle.query("status")
        assert await _runs(setup) == 3
        assert status["attempts"]["t-refine"] == 3, status["attempts"]
        await handle.signal("control", {"action": "stop"})
        await handle.result()
        history = await handle.fetch_history()

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if os.environ.get("CHOREGOS_ARCHIVER_HISTORIQUE"):
        target = HISTORIES / "wi-restarted-rerun-S22-41.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_a_work_item_parked_before_the_change_is_derived(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """Escalated before S22-41 (dev #4 and #6): no `parked` stored. The stage is derived from the
    state it came from — the transition out of it whose escalation leads to `needs_human`."""
    scripted(setup.adapters, FAILURE, FAILURE, refine_result("spec"))
    async with worker_factory():
        handle = await _escalate_then_restart(temporal_env, setup, legacy=True)
        await handle.signal("control", {"action": "rerun_stage", "by": "operator"})
        await _wait_state(handle, "awaiting_spec_approval")
        assert await _runs(setup) == 3
        await handle.signal("control", {"action": "stop"})
        await handle.result()


async def test_a_replay_the_old_code_dropped_does_not_block_the_next(
    setup: Fixture, temporal_env: Any, worker_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dev #4 received `rerun_stage` before the change, and dropped it: replayed, that request
    answers no to `patched`. The next request must still play — hence one patch id per request."""
    from choregos_orchestrator.workflows import interpreter

    dropped = f"{interpreter.ESCALATION_CONTEXT_AFTER_RESTART}-1"
    real = interpreter.workflow.patched
    monkeypatch.setattr(
        interpreter.workflow, "patched", lambda patch_id: False if patch_id == dropped else real(patch_id)
    )
    scripted(setup.adapters, FAILURE, FAILURE, refine_result("spec"))
    async with worker_factory():
        handle = await _escalate_then_restart(temporal_env, setup, legacy=True)
        await handle.signal("control", {"action": "rerun_stage", "by": "operator"})
        await asyncio.sleep(2)
        assert (await handle.query("status"))["state"] == "needs_human"
        assert await _runs(setup) == 2, "the first request is dropped, as the old code did"

        await handle.signal("control", {"action": "rerun_stage", "by": "operator"})
        await _wait_state(handle, "awaiting_spec_approval")
        assert await _runs(setup) == 3
        await handle.signal("control", {"action": "stop"})
        await handle.result()


async def test_an_answer_resumes_a_restarted_interpreter(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """`on_answer: resume` on a restarted interpreter: the answer brings the stage back."""
    from .test_reprise_apres_escalade import _demandes

    question = StageResult(
        status=StageStatus.NEEDS_HUMAN,
        summary="which currency?",
        reason="question",
        questions=[{"text": "Which currency for credit notes?", "options": ["EUR", "order currency"]}],
    )
    scripted(setup.adapters, question, refine_result("spec"))
    async with worker_factory():
        first = await start(temporal_env, setup)
        await _wait_state(first, "needs_human")
        await _wait_until(first, "the abandon is proposed", lambda s: s["pending_request"])
        await first.terminate(reason="the interpreter died (#497)")
        handle = await start(temporal_env, setup)
        await _wait_until(handle, "the restarted interpreter waits", lambda s: s["pending_request"])
        asked = next(d for d in await _demandes(setup) if d.kind == "question")

        await handle.signal(
            "human_decision",
            {
                "request_id": asked.id,
                "kind": "question",
                "approved": False,
                "answer": "the order currency",
                "decided_by": "operator",
            },
        )
        await _wait_state(handle, "awaiting_spec_approval")
        assert await _runs(setup) == 2
        await handle.signal("control", {"action": "stop"})
        await handle.result()


async def test_a_replay_on_a_work_item_that_did_not_escalate_is_still_dropped(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """Reading the database does not invent an escalation: on an ordinary approval, `rerun_stage`
    moves nothing (S22-08), and the wait does not spin."""
    scripted(setup.adapters, refine_result("spec"))
    async with worker_factory():
        first = await start(temporal_env, setup)
        await _wait_state(first, "awaiting_spec_approval")
        await _wait_mirrored(setup, "awaiting_spec_approval")  # not the query: it runs ahead (#748)
        await first.terminate(reason="the interpreter died (#497)")
        handle = await start(temporal_env, setup)
        await _wait_until(handle, "the restarted interpreter waits", lambda s: s["pending_request"])
        await handle.signal("control", {"action": "rerun_stage"})
        await asyncio.sleep(2)
        before = len((await handle.fetch_history()).events)
        await asyncio.sleep(2)
        assert len((await handle.fetch_history()).events) == before
        assert (await handle.query("status"))["state"] == "awaiting_spec_approval"
        assert await _runs(setup) == 1
        await handle.signal("control", {"action": "stop"})
        await handle.result()
