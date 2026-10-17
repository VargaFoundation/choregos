"""A restart after a pass whose decision was applied plays a NEW attempt (S22-49, #552).

S22-45 (#551) restores attempts from the runs on restart, but keeps a succeeded last run current, so
a run whose decision the dead interpreter never applied is read back rather than paid for twice.
For a transition the workflow plays again by itself — a review asking for changes sends the work
item back through `t-test` and `t-review` — a restart in that window read back the EARLIER pass's
succeeded runs: the review's old "changes requested" was applied a second time, and the work item
went round the loop once more for nothing. The run now remembers its decision (`runs.decided_at`).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from .conftest import Fixture, stage_result
from .test_gabarit_livraison_logicielle import DEPOT, _demarrer, _jusqu_a, _projet_dev, _ticket

pytestmark = pytest.mark.asyncio


async def _runs(item_id: str) -> list[tuple[str, str, bool]]:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (
            await session.execute(select(Run).where(Run.work_item_id == item_id).order_by(Run.created_at))
        ).scalars()
        return [(str(r.id), str(r.status), r.decided_at is not None) for r in rows]


async def _state(item_id: str) -> str:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        item = await session.get(WorkItem, item_id)
        assert item is not None
        return str(item.state)


async def _moves_to(item_id: str, state: str) -> int:
    """How many times the work item was moved to `state`, read from its events."""
    from choregos_api.db.models import Event
    from choregos_api.db.session import session_scope
    from choregos_contracts import EventType
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Event).where(
                    Event.work_item_id == item_id, Event.type == EventType.WORKITEM_STATE_CHANGED.value
                )
            )
        ).scalars()
        return sum(1 for event in rows if (event.payload or {}).get("to") == state)


async def _until(predicate: Any, what: str) -> None:
    for _ in range(600):
        if await predicate():
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"never reached: {what}")


async def test_a_restart_after_a_review_sent_the_work_item_back_plays_a_new_attempt(
    setup: Fixture, temporal_env: Any, worker_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The review asked for changes, the responder addressed them, and the interpreter died with the
    work item back in `implemented`: `t-test-1` and `t-review-1` succeeded and their decisions were
    applied. The restarted interpreter plays `t-test-2`, then `t-review-2` — the old code read both
    runs back, applied "changes requested" again and sent the work item round the loop once more."""
    from choregos_core.domain import ExecRef, ExecStatus, StageJobSpec
    from choregos_orchestrator.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_HEARTBEAT_SECONDS", "1")  # `await_run` polls every second
    reset_settings_cache()
    projet_id = await _projet_dev(setup)
    item_id, cle = await _ticket(setup, projet_id, 8, "dev-simple")
    setup.adapters.scm.set_diff(DEPOT, "main", "choregos/8", [("src/billing/total.py", 12, 3)])
    verdicts = iter(["request_changes", "approve"])
    played: list[str] = []

    def agent(stage_input: Any) -> Any:
        played.append(stage_input.run_id)
        transition = stage_input.transition.id
        if transition == "t-triage":
            return stage_result("small", outputs={"size": "S", "risk": "low"})
        if transition == "t-test":
            return stage_result("tests pass", outputs={"test_report": "412 passed"})
        if transition == "t-review":
            verdict = next(verdicts)
            return stage_result(f"review: {verdict}", outputs={"verdict": verdict, "review_markdown": "x"})
        if transition == "t-address-review":
            return stage_result(
                "addressed", outputs={"review_markdown": "done"}, artifacts={"branch": "choregos/8"}
            )
        return stage_result("fixed", artifacts={"branch": "choregos/8"})

    executor = setup.adapters.executor
    executor.handler = agent
    real_start = executor.start

    async def start_and_hold_the_responder(spec: StageJobSpec) -> ExecRef:
        ref = await real_start(spec)
        if spec.run_id.endswith("-t-address-review-1"):
            executor.statuses[spec.run_id] = ExecStatus(state="running")
        return ref

    executor.start = start_and_hold_the_responder
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            first = await _demarrer(temporal_env, setup, projet_id, item_id, cle)

            async def responder_running() -> bool:
                return any(
                    r.endswith("-t-address-review-1") and s == "running" for r, s, _ in await _runs(item_id)
                )

            await _until(responder_running, "the responder runs")
            # Paused now, the interpreter stops right after applying the responder's decision.
            await first.signal("control", {"action": "pause"})
            await _jusqu_a(first, "status", lambda s: s["paused"])
            responder = next(r for r, _, _ in await _runs(item_id) if r.endswith("-t-address-review-1"))
            executor.statuses[responder] = ExecStatus(
                state="succeeded", exit_code=0, result_url=f"fake://runs/{responder}/result.json"
            )

            async def back_in_implemented() -> bool:
                return await _state(item_id) == "implemented"

            await _until(back_in_implemented, "the work item is back in `implemented`")
            await first.terminate(reason="the interpreter died (#497)")
            before = await _runs(item_id)
            assert [run_id.split(f"{item_id}-", 1)[1] for run_id, _, _ in before] == [
                "t-triage-1",
                "t-implement-1",
                "t-test-1",
                "t-review-1",
                "t-address-review-1",
            ], before
            assert all(status == "succeeded" and decided for _, status, decided in before), before
            assert await _moves_to(item_id, "addressing_review") == 1
            played.clear()

            handle = await _demarrer(temporal_env, setup, projet_id, item_id, cle)
            settled = await _jusqu_a(handle, "status", lambda s: s["state"] in ("in_pr", "needs_human"))
            await handle.terminate(reason="the test ends at the pull request")

    assert settled["state"] == "in_pr", settled
    assert [run_id.split(f"{item_id}-", 1)[1] for run_id in played] == ["t-test-2", "t-review-2"], played
    assert await _moves_to(item_id, "addressing_review") == 1, "the earlier review was applied again"
