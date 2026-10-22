"""A run records its chain at stage start (S40-22, ADR 0077 §2).

`runs.initiated_by` is copied from the work item, `runs.inputs_from` names the run that produced the
revision the step reads. Both come from the platform's own rows, never from the agent, and a
replayed activity writes the same chain (ADR 0008).
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.asyncio

SPEC = "# Spec\n\nDeduct credit notes from the total, rounded half to even.\n"
INITIATOR = "person:ada@example.org"


async def _prepare(setup: Any) -> dict[str, Any]:
    from choregos_orchestrator.activities import stage as activities

    return await activities.prepare_stage(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "transition_id": "t-implement",
            "role": "implement",
            "actor": "dev",
            "from_state": "ready",
            "to_state": "in_progress",
            "attempt": 1,
        }
    )


async def _planned(setup: Any, *, document: str) -> None:
    """A `plan` run produced the spec; the work item carries `document` as its spec today."""
    from choregos_api.db.models import Project, Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services.artifacts import record_inline_outputs

    async with session_scope(orgs="*") as session:
        item = await session.get(WorkItem, setup.work_item_id)
        project = await session.get(Project, setup.project_id)
        assert item is not None and project is not None
        item.initiated_by = INITIATOR
        item.documents = {"spec_markdown": document}
        planner = Run(
            id="T-1-plan-1",
            work_item_id=item.id,
            project_id=project.id,
            stage_role="plan",
            status="succeeded",
            agent_slug="planner",
            agent_version=3,
        )
        session.add(planner)
        await session.flush()
        await record_inline_outputs(
            session, org_id=project.org_id, item=item, run=planner, outputs={"spec_markdown": SPEC}
        )


async def _chain_of(run_id: str) -> tuple[str | None, list[str] | None]:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        run = await session.get(Run, run_id)
        assert run is not None
        return run.initiated_by, run.inputs_from


async def test_a_step_records_who_started_it_and_the_run_it_reads_from(setup: Any) -> None:
    await _planned(setup, document=SPEC)
    plan = await _prepare(setup)
    run_id = str(plan["run_id"])
    assert SPEC in plan["stage_input"]["playbook"]["prompt"]
    assert await _chain_of(run_id) == (INITIATOR, ["T-1-plan-1"])

    # Replayed, the activity writes the same chain, not a second one.
    await _prepare(setup)
    assert await _chain_of(run_id) == (INITIATOR, ["T-1-plan-1"])


async def test_a_spec_a_person_edited_after_the_run_is_not_credited_to_that_run(setup: Any) -> None:
    await _planned(setup, document=SPEC + "\nEdited by a reviewer.\n")
    plan = await _prepare(setup)
    assert await _chain_of(str(plan["run_id"])) == (INITIATOR, [])


async def test_a_step_that_reads_nothing_records_an_empty_chain_not_none(setup: Any) -> None:
    plan = await _prepare(setup)
    initiated_by, inputs_from = await _chain_of(str(plan["run_id"]))
    assert inputs_from == [], "recorded: the step read nothing, unlike a run from before S40-22"
    assert initiated_by is None, "the fixture's work item was born outside any door"
