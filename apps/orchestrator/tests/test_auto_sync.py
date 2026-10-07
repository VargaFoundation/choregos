"""Un environnement `auto_sync` prévient ses tickets (#278, S21-27).

Argo suit `main` : il n'y a pas de départ. Le train y attendait `abort` sans rien lire, et un ticket
qui y montait attendait 72 h puis finissait chez un humain. Ici, le preset `solo` met `dev` en
`auto_sync` ; un VRAI `ReleaseTrain` y reçoit le ticket et le prévient.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
from typing import Any

import pytest

from .conftest import Fixture

pytestmark = pytest.mark.asyncio

HISTORIQUES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"

VERS_DEV = """apiVersion: choregos/v1
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


async def _ticket_vers_dev(setup: Fixture, temporal_env: Any) -> tuple[Any, Any]:
    """Le ticket, épinglé sur `to-dev` (un agent le prépare), poussé jusqu'au train ; et le train `dev`,
    lancé pour de vrai."""
    from choregos_api.db.models import Project, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.rbac import SYSTEM
    from choregos_api.services.definitions import publier_workflow
    from choregos_api.temporal import interpreter_id
    from choregos_orchestrator.train_client import fake_signals

    async with session_scope() as session:
        projet = await session.get(Project, setup.project_id)
        assert projet is not None
        definition = await publier_workflow(session, SYSTEM, projet, VERS_DEV)
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.workflow_def_id = definition.id
    ticket = await temporal_env.client.start_workflow(
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
    ((_, embarquement),) = fake_signals(train_id)
    train = await temporal_env.client.start_workflow(
        "ReleaseTrain", {"project_slug": setup.project_slug, "env": "dev"}, id=train_id, task_queue="test"
    )
    await train.signal("merged", {k: v for k, v in embarquement.items() if k != "start_payload"})
    return ticket, train


async def test_un_environnement_auto_sync_sain_livre_le_ticket(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    setup.adapters.cd.set_health("billing-api", "Healthy")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket, train = await _ticket_vers_dev(setup, temporal_env)
            issue = await asyncio.wait_for(ticket.result(), timeout=60)
            assert (await train.query("status_query"))["status"] == "auto_sync"
            await train.signal("abort", {"by": "test"})
            await train.result()
            historique = await train.fetch_history()
    assert issue["state"] == "live"

    # L'historique du train rejoue contre le code courant — et s'archive pour les suivants.
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(historique)
    if os.environ.get("CHOREGOS_ARCHIVER_HISTORIQUE"):
        cible = HISTORIQUES / "train-auto-sync-S21-27.json"
        cible.write_text(json.dumps(json.loads(historique.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_un_environnement_auto_sync_degrade_rend_le_ticket_a_un_humain(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    setup.adapters.cd.set_health("billing-api", "Degraded")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket, train = await _ticket_vers_dev(setup, temporal_env)
            for _ in range(600):
                if (await ticket.query("status"))["state"] == "needs_human":
                    break
                await asyncio.sleep(0.1)
            assert (await ticket.query("status"))["state"] == "needs_human"
            await train.signal("abort", {"by": "test"})
            await train.result()
            await ticket.cancel()
