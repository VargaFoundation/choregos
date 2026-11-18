"""A trial's work item never gets an interpreter, and a dying interpreter spares a trial's run (#1781).

Seen in production on a project whose catch-up pass lists the database's open work items: the trial
item (`state = "trial"`, key `TRIAL-<hex>`) was started on `WorkflowInterpreter`, whose first
activation died on `KeyError: 'trial'`, and `record_workflow_failure` then closed the trial's run and
revoked its gateway key before the agent's first model call.
"""

from __future__ import annotations

import pytest
from tranibor_core import WorkflowEngine
from tranibor_core.dsl import UnknownStateError
from tranibor_orchestrator.activities import tracker as tracker_activities
from tranibor_orchestrator.train_client import clear_fake_signals, fake_starts

from .conftest import Fixture
from .test_gateway_key_through_the_secret import _plan, _run

pytestmark = pytest.mark.asyncio


async def _make_trial(setup: Fixture) -> None:
    from tranibor_api.db.models import WorkItem
    from tranibor_api.db.session import session_scope

    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.state = "trial"


@pytest.mark.parametrize("owned_by_tracker", [False, True])
async def test_the_catch_up_pass_starts_no_interpreter_on_a_trial_item(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch, owned_by_tracker: bool
) -> None:
    clear_fake_signals()
    await _make_trial(setup)
    setup.adapters.tracker.items[setup.tracker_key].labels = ["agent-ready"]
    if not owned_by_tracker:
        monkeypatch.setattr(tracker_activities, "tracker_owns_items", lambda _tracker: False)

    result = await tracker_activities.reconcile_tracker({"project_slug": setup.project_slug})

    assert result["started"] == [], result
    assert not fake_starts()


async def test_a_dying_interpreter_leaves_a_trial_run_alone(setup: Fixture) -> None:
    from tranibor_api.db.models import Run
    from tranibor_api.db.session import session_scope
    from tranibor_orchestrator.activities import stage as activities
    from tranibor_orchestrator.activities.interpretation import record_workflow_failure

    prepared = await activities.prepare_stage(_plan(setup))
    async with session_scope() as session:
        run = await session.get(Run, prepared["run_id"])
        assert run is not None
        run.transition_id = "trial"
    await activities.start_run(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "stage_input": prepared["stage_input"],
        }
    )
    await record_workflow_failure(
        {"project_id": setup.project_id, "work_item_id": setup.work_item_id, "message": "KeyError: 'trial'"}
    )

    assert setup.adapters.gateway.revoked == [], "the trial's key is its own workflow's to revoke"
    assert (await _run(str(prepared["run_id"]))).spend_collected is False


async def test_an_unknown_state_is_a_named_error() -> None:
    from tranibor_core import parse_workflow

    wf, _ = parse_workflow(
        """apiVersion: choregos/v1
kind: Workflow
metadata: { name: tiny, version: 1 }
actors:
  bot: { type: agent, role: plan, model: "profile:standard" }
states:
  inbox: { display: Inbox, kind: wait }
  done: { display: Done, terminal: true }
transitions:
  - { id: t-go, from: inbox, to: done, by: bot }
"""
    )
    with pytest.raises(UnknownStateError, match="unknown state 'trial' in workflow 'tiny'") as info:
        WorkflowEngine(wf).is_terminal("trial")
    assert isinstance(info.value, KeyError)
