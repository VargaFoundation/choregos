"""An interpreter (re)started on a working work item plays a NEW attempt (S22-45, #550).

On the dev tenant (2026-10-15, v0.17.15), `mark_agent_ready` restarted the dead interpreters of
`choregos-sandbox-dev#4` and `#6`, in `live`. The new interpreter called `prepare_stage` for
`t-verify-prod` at attempt 1, which handed back the run `…-t-verify-prod-1` that had failed on
2026-10-10 (`reused: true`); `await_run` returned its old result at once, and the work item escalated
within a minute without any new attempt. A run still running when the interpreter died, on the
other hand, must be awaited again, not started twice (ADR 0008).
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
from typing import Any

import pytest
from choregos_contracts import StageResult, StageStatus

from .conftest import Fixture
from .test_gabarit_livraison_logicielle import (
    _demarrer,
    _etat,
    _jusqu_a,
    _projet_dev,
    _ticket,
    _verificateur_de_prod,
)

pytestmark = pytest.mark.asyncio

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"
FAILURE = StageResult(status=StageStatus.FAILED, summary="the smoke test failed", reason="agent_error")


async def _set_state(item_id: str, state: str) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        item = await session.get(WorkItem, item_id)
        assert item is not None
        item.state = state


async def _runs(item_id: str) -> list[tuple[str, str]]:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (
            await session.execute(select(Run).where(Run.work_item_id == item_id).order_by(Run.created_at))
        ).scalars()
        return [(str(r.id), str(r.status)) for r in rows]


async def _live_ticket(setup: Fixture) -> tuple[str, str, str]:
    """A `dev-simple` work item already in production, waiting for `t-verify-prod`."""
    projet_id = await _projet_dev(setup)
    item_id, cle = await _ticket(setup, projet_id, 4, "dev-simple")
    await _set_state(item_id, "live")
    return projet_id, item_id, cle


async def test_a_restart_after_a_failed_attempt_plays_the_next_one(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """`t-verify-prod-1` failed; the interpreter died with the work item in `live` and was started
    again. It plays `t-verify-prod-2` — the old code got run 1 back and escalated at once."""
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    projet_id, item_id, cle = await _live_ticket(setup)
    played: list[str] = []

    def verifier(stage_input: Any) -> Any:
        played.append(stage_input.run_id)
        return FAILURE if len(played) == 1 else _verificateur_de_prod()

    setup.adapters.executor.handler = verifier
    async with worker_factory():
        first = await _demarrer(temporal_env, setup, projet_id, item_id, cle)
        await _etat(first, "needs_human")
        # The abandon is proposed once the escalation is mirrored: no write of it is still in flight.
        await _jusqu_a(first, "status", lambda s: bool(s["pending_request"]))
        await first.terminate(reason="the interpreter died (#497)")
        # Where dev #4 stood: in `live`, its first verification failed, no interpreter.
        await _set_state(item_id, "live")
        assert [status for _, status in await _runs(item_id)] == ["failed"]

        handle = await _demarrer(temporal_env, setup, projet_id, item_id, cle)
        settled = await _jusqu_a(handle, "status", lambda s: s["state"] in ("verified", "needs_human"))
        assert settled["state"] == "verified", f"escalated without a new attempt: {settled}"
        outcome = await asyncio.wait_for(handle.result(), timeout=60)
        history = await handle.fetch_history()

    assert outcome["state"] == "verified", outcome
    runs = await _runs(item_id)
    assert [run_id.rsplit("-", 1)[1] for run_id, _ in runs] == ["1", "2"], runs
    assert len(played) == 2 and played[1].endswith("-t-verify-prod-2"), played
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if os.environ.get("CHOREGOS_ARCHIVER_HISTORIQUE"):
        target = HISTORIES / "wi-restarted-new-attempt-S22-45.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_a_restart_while_the_attempt_runs_awaits_it(
    setup: Fixture, temporal_env: Any, worker_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The interpreter dies while `t-verify-prod-1` runs: the restarted one awaits that run, and
    starts no second one — only a run that is over is a spent attempt."""
    from choregos_core.domain import ExecRef, ExecStatus, StageJobSpec
    from choregos_orchestrator.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_HEARTBEAT_SECONDS", "1")  # `await_run` polls every second
    reset_settings_cache()
    projet_id, item_id, cle = await _live_ticket(setup)
    executor = setup.adapters.executor
    executor.handler = lambda _stage_input: _verificateur_de_prod()
    real_start = executor.start

    async def start_and_keep_running(spec: StageJobSpec) -> ExecRef:
        ref = await real_start(spec)
        executor.statuses[spec.run_id] = ExecStatus(state="running")
        return ref

    executor.start = start_and_keep_running
    async with worker_factory():
        first = await _demarrer(temporal_env, setup, projet_id, item_id, cle)
        await _jusqu_a(first, "status", lambda s: bool(s["current_run"]))
        for _ in range(100):
            if [status for _, status in await _runs(item_id)] == ["running"]:
                break
            await asyncio.sleep(0.1)
        await first.terminate(reason="the interpreter died (#497)")

        handle = await _demarrer(temporal_env, setup, projet_id, item_id, cle)
        status = await _jusqu_a(handle, "status", lambda s: bool(s["current_run"]))
        ((run_id, _),) = await _runs(item_id)
        assert status["current_run"] == run_id and run_id.endswith("-t-verify-prod-1"), status
        executor.statuses[run_id] = ExecStatus(
            state="succeeded", exit_code=0, result_url=f"fake://runs/{run_id}/result.json"
        )
        outcome = await asyncio.wait_for(handle.result(), timeout=60)

    assert outcome["state"] == "verified", outcome
    assert len(await _runs(item_id)) == 1
    assert executor.started and len(executor.started) == 1


async def test_a_restart_after_a_succeeded_run_reads_it_back(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """`t-verify-prod-1` succeeded, but the interpreter died before its decision was recorded: the
    restarted one reads that run's result back and moves on — no second run, which would cost the
    stage again and could push its commits twice."""
    projet_id, item_id, cle = await _live_ticket(setup)
    executor = setup.adapters.executor
    executor.handler = lambda _stage_input: _verificateur_de_prod()
    async with worker_factory():
        first = await _demarrer(temporal_env, setup, projet_id, item_id, cle)
        await asyncio.wait_for(first.result(), timeout=60)
        # Where the work item stands if the interpreter died between the run and its decision.
        await _set_state(item_id, "live")
        assert [status for _, status in await _runs(item_id)] == ["succeeded"]

        handle = await _demarrer(temporal_env, setup, projet_id, item_id, cle)
        outcome = await asyncio.wait_for(handle.result(), timeout=60)

    assert outcome["state"] == "verified", outcome
    runs = await _runs(item_id)
    assert len(runs) == 1 and runs[0][0].endswith("-t-verify-prod-1"), runs
    assert len(executor.started) == 1, [spec.run_id for spec in executor.started]
