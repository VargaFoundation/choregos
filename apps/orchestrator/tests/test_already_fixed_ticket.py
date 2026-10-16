"""A ticket whose fix is already on `main` closes as already fixed, on measured evidence only (S22-47).

On the dev tenant, `DiametralGroup/choregos-sandbox-dev#18` and `#21` (`dev-simple`, #539): the bug
was already fixed on `main`, the implementation changed nothing, and the work item went on to test,
review, then a pull request from a branch that did not exist. `dev-simple` v2 declares
`t-already-fixed`: after an implementation that MEASURED no change, the platform reads
`already_fixed_proven` — no branch, tests run and green on that unchanged checkout — and the work
item ends `already_fixed`, its issue closed with the evidence. Without that evidence the
implementation escalates at once. Real `dev-simple`, fakes, Temporal's test server.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
from typing import Any

import pytest

from .conftest import Fixture, stage_result
from .test_gabarit_livraison_logicielle import (
    DEPOT,
    _agents,
    _demandes,
    _demarrer,
    _etat,
    _jusqu_a,
    _projet_dev,
    _runs,
    _ticket,
)

pytestmark = pytest.mark.asyncio

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"
MARKER = "<!-- choregos:already-fixed -->"
UNPROVEN = "the implementation changed nothing and the evidence does not prove the fix already exists"


def _unchanged(*, tests_measured: bool = True) -> Any:
    """What the runner reports for an implementation that changed nothing: git's zero diff, no
    commit — and, when it ran them, the tests on that unchanged checkout, which is `main`."""
    from choregos_contracts import Evidence

    # Without `tests_measured`, the agent SAYS the tests pass: its word, not evidence.
    evidence = Evidence(tests_passed=True, tests_run=412, tests_failed=0, diff_files=0, diff_lines=0)
    measured = ["diff_files", "diff_lines"]
    if tests_measured:
        measured += ["tests_failed", "tests_passed", "tests_run"]
    evidence.measured = sorted(measured)
    return stage_result("Already fixed on main: nothing to change", evidence=evidence)


def _triage() -> Any:
    return stage_result("small", outputs={"size": "S", "risk": "low"})


def _approve() -> Any:
    return stage_result("review: approve", outputs={"verdict": "approve", "review_markdown": "Looks right."})


def _pr_calls(setup: Fixture, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    real = setup.adapters.scm.open_pr

    async def open_pr(repo: str, head: str, *args: Any, **kwargs: Any) -> Any:
        calls.append(head)
        return await real(repo, head, *args, **kwargs)

    monkeypatch.setattr(setup.adapters.scm, "open_pr", open_pr)
    return calls


async def _arrivals(item_id: str, state: str) -> list[dict[str, Any]]:
    from choregos_api.db.models import Event
    from choregos_api.db.session import session_scope
    from choregos_contracts import EventType
    from sqlalchemy import select

    async with session_scope() as session:
        rows = await session.execute(
            select(Event)
            .where(Event.work_item_id == item_id, Event.type == str(EventType.WORKITEM_STATE_CHANGED))
            .order_by(Event.ts)
        )
        return [dict(e.payload) for e in rows.scalars() if e.payload.get("to") == state]


async def _request_payloads(item_id: str) -> list[dict[str, Any]]:
    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = await session.execute(select(HumanRequest).where(HumanRequest.work_item_id == item_id))
        return [dict(r.payload or {}) for r in rows.scalars()]


async def test_a_no_change_ticket_green_on_main_closes_as_already_fixed_without_a_pull_request(
    setup: Fixture, temporal_env: Any, worker_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    project_id = await _projet_dev(setup)
    item_id, key = await _ticket(setup, project_id, 18, "dev-simple")
    calls = _pr_calls(setup, monkeypatch)
    setup.adapters.executor.handler = _agents({"t-triage": _triage, "t-implement": _unchanged})
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket = await _demarrer(temporal_env, setup, project_id, item_id, key)
            result = await asyncio.wait_for(ticket.result(), timeout=90)
            history = await ticket.fetch_history()

    assert result["state"] == "already_fixed"
    assert [t for t, _ in await _runs(item_id)] == ["t-triage", "t-implement"], "no test, no review"
    assert calls == [] and setup.adapters.scm.prs == {}, "no pull request is attempted"
    assert await _demandes(item_id) == [], "nobody is called"
    (arrival,) = await _arrivals(item_id, "already_fixed")
    assert arrival["from"] == "triaged", arrival
    assert arrival["reason"].startswith("already fixed: nothing to change"), arrival

    # The issue is closed as completed, with a comment citing the measured evidence.
    tracker = setup.adapters.tracker
    assert tracker.items[key].closed_at is not None, "the issue stays open"
    comment = tracker.status_comment(key, MARKER)
    assert comment is not None
    assert "0 files, 0 lines, no commit, no branch pushed" in comment, comment
    assert "412 run, 0 failed" in comment and "Already fixed" in comment, comment

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if os.environ.get("CHOREGOS_ARCHIVER_HISTORIQUE"):
        target = HISTORIES / "wi-already-fixed-S22-47.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_a_no_change_ticket_without_measured_tests_escalates_at_once(
    setup: Fixture, temporal_env: Any, worker_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = await _projet_dev(setup)
    item_id, key = await _ticket(setup, project_id, 21, "dev-simple")
    calls = _pr_calls(setup, monkeypatch)
    setup.adapters.executor.handler = _agents(
        {"t-triage": _triage, "t-implement": lambda: _unchanged(tests_measured=False)}
    )
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket = await _demarrer(temporal_env, setup, project_id, item_id, key)
            await _etat(ticket, "needs_human")
            await _jusqu_a(ticket, "status", lambda s: bool(s["pending_request"]))
            description = await ticket.describe()
            assert description.status is not None and description.status.name == "RUNNING"
            await ticket.cancel()

    assert [t for t, _ in await _runs(item_id)] == ["t-triage", "t-implement"], "no retry, no test"
    assert calls == []
    (arrival,) = await _arrivals(item_id, "needs_human")
    assert arrival["from"] == "triaged" and arrival["reason"].startswith(UNPROVEN), arrival
    assert "did not run the tests" in arrival["reason"], arrival
    asked = [p for p in await _request_payloads(item_id) if p.get("reason") == "no_change_unproven"]
    assert asked and UNPROVEN in asked[0]["question"], asked
    assert setup.adapters.tracker.items[key].closed_at is None, "an unproven ticket is not closed"


async def test_a_ticket_with_changes_is_unaffected(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    from choregos_contracts import Evidence

    project_id = await _projet_dev(setup)
    item_id, key = await _ticket(setup, project_id, 7, "dev-simple")
    setup.adapters.scm.set_diff(DEPOT, "main", "choregos/7", [("src/billing/total.py", 12, 3)])
    evidence = Evidence(tests_passed=True, tests_run=412, tests_failed=0, diff_files=1, diff_lines=15)
    evidence.measured = ["diff_files", "diff_lines", "tests_failed", "tests_passed", "tests_run"]
    setup.adapters.executor.handler = _agents(
        {
            "t-triage": _triage,
            "t-implement": lambda: stage_result(
                "fixed", evidence=evidence, artifacts={"branch": "choregos/7", "commits": ["fix(billing)"]}
            ),
            "t-test": lambda: stage_result("tests pass", outputs={"test_report": "412 passed"}),
            "t-review": _approve,
        }
    )
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket = await _demarrer(temporal_env, setup, project_id, item_id, key)
            await _etat(ticket, "in_pr")
            await ticket.cancel()
    assert [t for t, _ in await _runs(item_id)] == ["t-triage", "t-implement", "t-test", "t-review"]
    assert await _arrivals(item_id, "already_fixed") == []


async def test_a_history_before_the_marker_goes_on_to_the_tests(
    setup: Fixture, temporal_env: Any, worker_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run recorded before the marker answers no to `patched`: the empty change is tested and
    reviewed, then escalates at the pull request (S22-46), as then."""
    from choregos_orchestrator.workflows import interpreter

    real = interpreter.workflow.patched
    monkeypatch.setattr(
        interpreter.workflow,
        "patched",
        lambda patch_id: False if patch_id == interpreter.ALREADY_FIXED_ON_EVIDENCE else real(patch_id),
    )
    project_id = await _projet_dev(setup)
    item_id, key = await _ticket(setup, project_id, 22, "dev-simple")
    setup.adapters.executor.handler = _agents(
        {
            "t-triage": _triage,
            "t-implement": _unchanged,
            "t-test": lambda: stage_result("tests pass", outputs={"test_report": "412 passed"}),
            "t-review": _approve,
        }
    )
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket = await _demarrer(temporal_env, setup, project_id, item_id, key)
            await _etat(ticket, "needs_human")
            await ticket.cancel()
    assert [t for t, _ in await _runs(item_id)] == ["t-triage", "t-implement", "t-test", "t-review"]
    assert await _arrivals(item_id, "already_fixed") == []


@pytest.mark.parametrize(
    ("branch", "passed", "detail"),
    [
        ("absent", True, "nothing to change"),
        ("pushed", False, "a work branch was pushed"),
        ("502", False, ""),
    ],
)
async def test_the_guarantee_reads_the_branch_from_the_scm(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch, branch: str, passed: bool, detail: str
) -> None:
    """No branch on the SCM is part of the proof: a pushed one refuses, and so does an SCM that
    cannot answer — an outage proves nothing."""
    from choregos_adapters.errors import UpstreamError
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.gates import evaluate_gates

    if branch != "pushed":  # the fixture's work item has a scripted branch: here it was never pushed
        setup.adapters.scm.delete_branch("varga/billing-api", "choregos/123")
    if branch == "502":

        async def unreadable(repo: str, name: str) -> bool:
            raise UpstreamError("github", f"GET ref {name}", status_code=502)

        monkeypatch.setattr(setup.adapters.scm, "branch_exists", unreadable)
    async with session_scope() as session:
        run = Run(
            id=f"{setup.work_item_id}-t-implement-1",
            work_item_id=setup.work_item_id,
            project_id=setup.project_id,
            transition_id="t-implement",
            stage_role="implement",
            attempt=1,
            status="succeeded",
            result=_unchanged().model_dump(mode="json", by_alias=True),
        )
        session.add(run)
    payload = {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "run_id": f"{setup.work_item_id}-t-implement-1",
        "gates": [{"name": "already_fixed_proven"}],
    }
    (verdict,) = await evaluate_gates(payload)
    assert verdict["passed"] is passed, verdict
    assert detail in verdict["detail"], verdict
    if branch == "502":
        assert "could not be read" in verdict["detail"], verdict
