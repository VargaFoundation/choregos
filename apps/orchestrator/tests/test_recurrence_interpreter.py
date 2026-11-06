"""A new pass of a work item whose ticket came back (C1, D2C plan §4), played by the real interpreter.

The pass is the next Temporal run of the same interpreter id, started after the first one COMPLETED, at
the workflow's start state. Its runs are numbered past the first pass's (`<work item>-<transition>-<n>`
names a run: handing an old one back would read its old result, ADR 0008), and its attempts budget
starts at zero: a finding that comes back gets the same tries as the first time. Real interpreter, fakes,
Temporal's test server; the history of the second pass replays and is archived.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from typing import Any

import pytest
from choregos_contracts import StageResult, StageStatus

from .conftest import Fixture, recording_history, stage_result
from .kits.train_and_work_item import wait_until_state

pytestmark = pytest.mark.asyncio

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"

FIX = """apiVersion: choregos/v1
kind: Workflow
metadata: { name: fix-a-finding, version: 1 }
initial: inbox
actors:
  developer: { type: agent, role: implement, model: "profile:standard" }
  lead: { type: human, group: maintainers, sla_hours: 8 }
states:
  inbox: { display: To fix }
  resolved: { display: Resolved, terminal: true }
  needs_human: { display: Needs a human, kind: wait }
  abandoned: { display: Abandoned, terminal: true }
transitions:
  - id: t-fix
    from: inbox
    to: resolved
    by: developer
    on_fail: { to: inbox, max_attempts: 2, escalate_to: needs_human }
  - { id: t-abandon, from: needs_human, to: abandoned, by: lead }
defaults:
  needs_human: { on_abandon: abandoned }
"""


def _failed(why: str) -> StageResult:
    return StageResult(status=StageStatus.FAILED, summary=why, reason="agent_error")


async def _pin(setup: Fixture) -> None:
    from choregos_api.db.models import Project, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.rbac import SYSTEM
    from choregos_api.services.definitions import publish_workflow

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        definition = await publish_workflow(session, SYSTEM, project, FIX)
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.workflow_def_id = definition.id


async def _reopen(setup: Fixture) -> dict[str, Any]:
    """What a door does when the ticket comes back: claim the pass; returns the interpreter's payload."""
    from choregos_api.db.models import Project, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services.recurrence import reopen_work_item, restart_payload

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        item = await session.get(WorkItem, setup.work_item_id)
        assert project is not None and item is not None
        assert await reopen_work_item(session, project, item, door="test", reason="the finding is back") == 1
        return restart_payload(project, item)


async def _runs(setup: Fixture) -> list[tuple[str, str]]:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Run).where(Run.work_item_id == setup.work_item_id).order_by(Run.created_at)
            )
        ).scalars()
        return [(str(r.id).rsplit("-", 1)[1], str(r.status)) for r in rows]


async def test_a_new_pass_numbers_its_runs_past_the_first_and_gets_a_fresh_attempts_budget(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    from choregos_api.temporal import interpreter_id
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
    from temporalio.worker import Replayer

    await _pin(setup)
    answers = [
        _failed("the chart pins 1.2.0"),
        stage_result("tag bumped"),
        _failed("lint"),
        stage_result("ok"),
    ]
    played: list[str] = []

    def developer(stage_input: Any) -> Any:
        played.append(stage_input.run_id)
        return answers[len(played) - 1]

    setup.adapters.executor.handler = developer
    workflow_id = interpreter_id(setup.project_slug, setup.tracker_key)
    async with worker_factory():
        first = await temporal_env.client.start_workflow(
            "WorkflowInterpreter",
            {
                "project_id": setup.project_id,
                "project_slug": setup.project_slug,
                "work_item_id": setup.work_item_id,
                "tracker_key": setup.tracker_key,
            },
            id=workflow_id,
            task_queue="test",
        )
        assert (await asyncio.wait_for(first.result(), timeout=60))["state"] == "resolved"

        payload = await _reopen(setup)
        assert payload["resume_from"] == "inbox" and payload["recurrence"] == 1
        # The id's next run, after a COMPLETED one: what `restart_interpreter` asks Temporal.
        second = await temporal_env.client.start_workflow(
            "WorkflowInterpreter",
            payload,
            id=workflow_id,
            task_queue="test",
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
            id_conflict_policy=WorkflowIDConflictPolicy.TERMINATE_EXISTING,
        )
        settled = await wait_until_state(
            second, "status", lambda s: s["state"] in ("resolved", "needs_human")
        )
        assert settled["state"] == "resolved", f"the first pass's attempts were counted: {settled}"
        outcome = await asyncio.wait_for(second.result(), timeout=60)
        history = await second.fetch_history()

    assert outcome["state"] == "resolved"
    assert first.result_run_id != second.result_run_id, "a second run of the same id"
    assert await _runs(setup) == [("1", "failed"), ("2", "succeeded"), ("3", "failed"), ("4", "succeeded")]
    assert [run_id.rsplit("-", 1)[1] for run_id in played] == ["1", "2", "3", "4"]
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        target = HISTORIES / "wi-recurrence-fresh-attempts-C1.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_a_new_pass_reads_none_of_the_previous_passs_outputs_or_spend(setup: Fixture) -> None:
    """`load_context` of a new pass: no last outputs, no spend, the earlier attempts named."""
    from choregos_api.db.models import CostLedger, Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow
    from choregos_orchestrator.activities.interpretation import load_context

    await _pin(setup)
    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.state, item.closed_at = "resolved", utcnow()
        session.add(
            Run(
                id=f"{setup.work_item_id}-t-fix-2",
                work_item_id=setup.work_item_id,
                project_id=setup.project_id,
                transition_id="t-fix",
                stage_role="implement",
                attempt=2,
                status="succeeded",
                decided_at=utcnow(),
                ended_at=utcnow(),
                result=stage_result("tag bumped", outputs={"fixed": True}).model_dump(mode="json"),
            )
        )
        session.add(
            CostLedger(
                project_id=setup.project_id, work_item_id=setup.work_item_id, cost_usd=4.5, ts=utcnow()
            )
        )
    before = await load_context({"project_id": setup.project_id, "work_item_id": setup.work_item_id})
    assert before["ticket_cost_usd"] == 4.5 and before["last_outputs"] == {"fixed": True}
    assert "attempts_before_pass" not in before

    await _reopen(setup)
    after = await load_context({"project_id": setup.project_id, "work_item_id": setup.work_item_id})
    assert after["state"] == "inbox"
    assert after["ticket_cost_usd"] == 0.0 and after["last_outputs"] is None
    assert after["attempts_before_pass"] == {"t-fix": 2}
    assert after["attempts_spent"] == {"t-fix": 2}, "the run numbers go on past the first pass's"
