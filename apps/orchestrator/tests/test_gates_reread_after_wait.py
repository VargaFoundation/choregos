"""S22-32 (#359) — a transition's synchronous guarantees are read again once its pending ones resolve.

`t-merge` of the `study` workflow carries `ci_green` (pending until the CI reports) and
`adr_number_free` (read on the default branch). Another study merges an ADR with the same number
WHILE the CI runs: the duplicate must be refused when the CI turns green, not merged on the verdict
read when the transition was entered. Played on the fakes with a real interpreter; the history is
archived for `tests/replay` (`CHOREGOS_RECORD_HISTORY=1`).
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from typing import Any

import pytest

from .conftest import Fixture, recording_history, stage_result
from .kits.software_delivery import (
    PROPOSED_ADR,
    REPOSITORY,
    create_dev_project,
    create_ticket,
    decide,
    report_ci_result,
    runs_of,
    scripted_agents,
    start_interpreter,
    study_results,
    wait_for_state,
)

pytestmark = pytest.mark.asyncio

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"


async def _gate_journal(item_id: str) -> list[dict[str, Any]]:
    """Every guarantee verdict journaled for the work item's runs, in order."""
    from choregos_api.db.models import Run, RunEvent
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        runs = (await session.execute(select(Run.id).where(Run.work_item_id == item_id))).scalars().all()
        rows = (
            (
                await session.execute(
                    select(RunEvent)
                    .where(RunEvent.run_id.in_(list(runs)), RunEvent.type == "gate.outcome")
                    .order_by(RunEvent.ts, RunEvent.seq)
                )
            )
            .scalars()
            .all()
        )
        return [dict(r.payload) for r in rows]


async def _ci_awaited(item_id: str) -> None:
    """`t-merge` has been entered: `ci_green` was read pending, `adr_number_free` free."""
    for _ in range(600):
        journal = await _gate_journal(item_id)
        if any(v["name"] == "ci_green" and v["pending"] for v in journal):
            free = [v for v in journal if v["name"] == "adr_number_free"]
            assert free and free[-1]["passed"], free
            return
        await asyncio.sleep(0.1)
    raise AssertionError("the CI was never awaited")


async def test_an_adr_number_taken_during_the_ci_is_refused_when_the_ci_turns_green(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    projet_id = await create_dev_project(setup)
    item_id, cle = await create_ticket(setup, projet_id, 14, "study")
    scm = setup.adapters.scm
    accepted = PROPOSED_ADR.replace('status: "proposed"', 'status: "accepted"\n+date: 2026-10-14')
    scm.set_diff(REPOSITORY, "main", "choregos/14", [("docs/adr/0001-queue.md", 17, 0, PROPOSED_ADR)])
    acceptances: list[str] = []

    def accept() -> Any:
        # First time: the status becomes `accepted`. Second: the architect renumbers to 0002.
        path = "docs/adr/0001-queue.md" if not acceptances else "docs/adr/0002-queue.md"
        acceptances.append(path)
        scm.set_diff(REPOSITORY, "main", "choregos/14", [(path, 18, 0, accepted)])
        return stage_result("ADR accepted")

    setup.adapters.executor.handler = scripted_agents({**study_results(PROPOSED_ADR), "t-accept": accept})
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket = await start_interpreter(temporal_env, setup, projet_id, item_id, cle)
            await wait_for_state(ticket, "awaiting_decision")
            await decide(ticket)
            await wait_for_state(ticket, "in_pr")
            await _ci_awaited(item_id)
            # Another study merges its own 0001 while this one's CI runs.
            await scm.commit_files(REPOSITORY, "main", {"docs/adr/0001-cache.md": "# Cache\n"}, "other study")
            await report_ci_result(setup, ticket, "success", "ci-14")
            outcome = await asyncio.wait_for(ticket.result(), timeout=90)
            history = await ticket.fetch_history()

    assert outcome["state"] == "adopted"
    assert acceptances == ["docs/adr/0001-queue.md", "docs/adr/0002-queue.md"], (
        "the duplicate found during the CI sent the study back to the architect"
    )
    assert [t for t, _ in await runs_of(item_id)][-2:] == ["t-accept", "t-accept"]
    assert len(scm.merge_queue) == 1, "merged once, after the renumbering"
    refused = [v for v in await _gate_journal(item_id) if v["name"] == "adr_number_free" and not v["passed"]]
    assert refused and "already taken on the default branch" in refused[0]["detail"]

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        target = HISTORIES / "wi-gates-reread-after-wait-S22-32.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")
