"""`scope_respected` judges what the RUN wrote, not the whole ticket (S22-11).

On the dev tenant, on 10/09, the ADR of study #5 went red on "3 file(s) outside the allowed
paths": the Makefile and two files pushed by the FRAMING step, measured from `main`.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.asyncio

REPO = "varga/billing-api"
BRANCH = "choregos/123"
RULE = {"name": "scope_respected", "params": {"paths": ["docs/adr/**"]}}


async def _run_starting_at(setup: Any, start: str | None) -> str:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_contracts import StageResult, StageStatus

    result = StageResult(status=StageStatus.DONE, summary="ADR written")
    result.artifacts.branch = BRANCH
    if start:
        result.artifacts.reports["start_commit"] = start
    async with session_scope() as session:
        run = Run(
            id=f"{setup.work_item_id}-t-draft-1",
            work_item_id=setup.work_item_id,
            project_id=setup.project_id,
            transition_id="t-draft",
            stage_role="architect",
            attempt=1,
            status="succeeded",
            result=result.model_dump(mode="json", by_alias=True),
        )
        session.add(run)
    return run.id


async def test_the_work_of_earlier_steps_does_not_count(setup: Any) -> None:
    from choregos_orchestrator.activities.gates import evaluate_gates

    setup.adapters.scm.set_diff(
        REPO,
        "main",
        BRANCH,
        [("Makefile", 4, 0), ("sitecustomize.py", 3, 0), ("docs/adr/0001-x.md", 30, 0)],
    )
    setup.adapters.scm.set_diff(REPO, "abc123", BRANCH, [("docs/adr/0001-x.md", 30, 0)])
    run_id = await _run_starting_at(setup, "abc123")

    charge = {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "run_id": run_id,
        "gates": [RULE],
    }
    [verdict] = await evaluate_gates(charge)
    assert verdict["passed"] is True, verdict


async def test_what_the_run_writes_out_of_perimeter_still_counts(setup: Any) -> None:
    from choregos_orchestrator.activities.gates import evaluate_gates

    setup.adapters.scm.set_diff(REPO, "abc123", BRANCH, [("Makefile", 4, 0), ("docs/adr/0001-x.md", 30, 0)])
    run_id = await _run_starting_at(setup, "abc123")
    charge = {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "run_id": run_id,
        "gates": [RULE],
    }
    [verdict] = await evaluate_gates(charge)
    assert verdict["passed"] is False and verdict["annotations"] == ["Makefile"], verdict


async def test_a_run_that_does_not_say_where_it_starts_is_judged_on_the_ticket(setup: Any) -> None:
    """A runner from before S22-11: nothing changes, the ticket diff applies."""
    from choregos_orchestrator.activities.gates import evaluate_gates

    setup.adapters.scm.set_diff(REPO, "main", BRANCH, [("Makefile", 4, 0), ("docs/adr/0001-x.md", 30, 0)])
    run_id = await _run_starting_at(setup, None)
    charge = {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "run_id": run_id,
        "gates": [RULE],
    }
    [verdict] = await evaluate_gates(charge)
    assert verdict["passed"] is False, verdict
