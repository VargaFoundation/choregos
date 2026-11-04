"""An `auto_sync` environment warns its work items (#278, S21-27).

Argo follows `main`: there is no departure. The train waited there for `abort` without reading
anything, and a work item that boarded waited 72 h then ended with a human. Here, the `solo` preset
puts `dev` in `auto_sync`; a REAL `ReleaseTrain` receives the work item there and warns it.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from typing import Any

import pytest

from .conftest import Fixture, recording_history

pytestmark = pytest.mark.asyncio

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"

TO_DEV = """apiVersion: choregos/v1
kind: Workflow
metadata: { name: to-dev, version: 1 }
actors:
  bot: { type: agent, role: plan, model: "profile:standard" }
  ops: { type: human, group: ops, sla_hours: 8 }
states:
  inbox: { display: Inbox, kind: wait }
  ready: { display: Ready to ship }
  live: { display: Live in dev, terminal: true }
  needs_human: { display: Needs a human, kind: wait }
  abandoned: { display: Abandoned, terminal: true }
transitions:
  - { id: t-go, from: inbox, to: ready, by: bot }
  - { id: t-ship, from: ready, to: live, via: release_train, train: { env: dev }, timeout_hours: 4 }
  - { id: t-abandon, from: needs_human, to: abandoned, by: ops }
defaults:
  from_any_agent_state: { on_question: needs_human }
  needs_human: { on_answer: resume, on_abandon: abandoned }
"""


async def _work_item_to_dev(setup: Fixture, temporal_env: Any) -> tuple[Any, Any]:
    """The work item, pinned to `to-dev` (an agent prepares it), pushed up to the train; and the `dev`
    train, launched for real."""
    from choregos_api.db.models import Project, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.rbac import SYSTEM
    from choregos_api.services.definitions import publish_workflow
    from choregos_api.temporal import interpreter_id
    from choregos_orchestrator.train_client import fake_signals

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        definition = await publish_workflow(session, SYSTEM, project, TO_DEV)
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.workflow_def_id = definition.id
    work_item_run = await temporal_env.client.start_workflow(
        "WorkflowInterpreter",
        {
            "project_id": setup.project_id,
            "project_slug": setup.project_slug,
            "work_item_id": setup.work_item_id,
            "tracker_key": setup.tracker_key,
        },
        id=interpreter_id(setup.project_slug, setup.tracker_key),
        task_queue="test",
    )
    train_id = f"train-{setup.project_slug}-dev"
    for _ in range(600):
        if fake_signals(train_id):
            break
        await asyncio.sleep(0.1)
    ((_, boarding),) = fake_signals(train_id)
    train = await temporal_env.client.start_workflow(
        "ReleaseTrain", {"project_slug": setup.project_slug, "env": "dev"}, id=train_id, task_queue="test"
    )
    await train.signal("merged", {k: v for k, v in boarding.items() if k != "start_payload"})
    return work_item_run, train


async def test_a_healthy_auto_sync_environment_delivers_the_work_item(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    setup.adapters.cd.set_health("billing-api", "Healthy")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            work_item_run, train = await _work_item_to_dev(setup, temporal_env)
            issue = await asyncio.wait_for(work_item_run.result(), timeout=60)
            assert (await train.query("status_query"))["status"] == "auto_sync"
            await train.signal("abort", {"by": "test"})
            await train.result()
            history = await train.fetch_history()
    assert issue["state"] == "live"

    # The train's history replays against the current code — and is archived for the next ones.
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        english = recording_history() == "english"  # S34-02
        # S45-39: `english-keys` archives the history that sends English keys, and only that one.
        target = HISTORIES / (
            "train-english-keys-S45-39.json"
            if recording_history() == "english-keys"
            else "train-auto-sync-english-S34-02.json"
            if english
            else "train-auto-sync-S21-27.json"
        )
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_a_degraded_auto_sync_environment_hands_the_work_item_to_a_human(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    setup.adapters.cd.set_health("billing-api", "Degraded")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            work_item_run, train = await _work_item_to_dev(setup, temporal_env)
            for _ in range(600):
                if (await work_item_run.query("status"))["state"] == "needs_human":
                    break
                await asyncio.sleep(0.1)
            assert (await work_item_run.query("status"))["state"] == "needs_human"
            await train.signal("abort", {"by": "test"})
            await train.result()
            await work_item_run.cancel()
