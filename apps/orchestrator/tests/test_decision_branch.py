"""A workflow branches on a decision model's answer and its probability (ADR 0121, S52-04).

"Which team handles it?" — a decision step answers in milliseconds, through the gateway, and
`when: { route: billing, route_confidence: { gte: 0.8 } }` takes the billing branch; anything less
sure goes to a person. The real interpreter on Temporal's test server (its histories replay and are
archived for the next changes), then the in-memory engine of `make demo` and `choregos dev simulate`.
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

ROUTE = """apiVersion: choregos/v1
kind: Workflow
metadata: { name: route-by-decision, version: 1 }
initial: inbox
actors:
  router:
    type: decision
    role: route_ticket
    state: [title, description, fields.department]
    questions:
      route: { type: choice, instructions: "Which team handles it?", options: [billing, tech, other] }
      urgent: { type: yes_no, instructions: "Must it be handled today?" }
  platform: { type: system }
  lead: { type: human, group: maintainers, sla_hours: 8 }
states:
  inbox: { display: To do }
  routed: { display: Routed }
  billing: { display: Billing, terminal: true }
  triage: { display: Triage by a person, kind: wait }
  done: { display: Done, terminal: true }
  needs_human: { display: Needs a human, kind: wait }
transitions:
  - id: t-route
    from: inbox
    to: routed
    by: router
    on_fail: { to: inbox, max_attempts: 2, escalate_to: needs_human }
  - id: t-billing
    from: routed
    to: billing
    by: platform
    when: { route: billing, route_confidence: { gte: 0.8 } }
  - { id: t-triage, from: routed, to: triage, by: platform }
  - { id: t-decide, from: triage, to: done, by: lead }
  - { id: t-give-up, from: needs_human, to: done, by: lead }
"""


async def _publish(setup: Fixture) -> None:
    """The workflow, saved through the API's only write path, and pinned on the work item."""
    from choregos_api.db.models import ModelCatalogueRow, Project, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.rbac import SYSTEM
    from choregos_api.services.definitions import publish_workflow

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        session.add(ModelCatalogueRow(org_id=project.org_id, model_name="platform/decide", usages=["decide"]))
        await session.flush()
        definition = await publish_workflow(session, SYSTEM, project, ROUTE)
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.workflow_def_id = definition.id
        item.fields = {"department": "accounts payable"}


async def _start(setup: Fixture, temporal_env: Any, *, publish: bool = True) -> Any:
    from choregos_api.temporal import interpreter_id

    if publish:
        await _publish(setup)
    return await temporal_env.client.start_workflow(
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


async def _replay_and_archive(handle: Any, name: str) -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    history = await handle.fetch_history()
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        target = HISTORIES / name
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def _state(setup: Fixture) -> str:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        return item.state


async def test_a_sure_answer_takes_the_branch_it_names(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    setup.adapters.decisions.answer(route=("billing", 0.93), urgent=(False, 0.8))
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            handle = await _start(setup, temporal_env)
            outcome = await asyncio.wait_for(handle.result(), timeout=60)
            await _replay_and_archive(handle, "wi-decision-route-billing-S52-04.json")
    assert outcome["state"] == "billing"
    (call,) = setup.adapters.decisions.calls
    assert call.state == {
        "title": "Les avoirs ne sont pas déduits du total",  # lang: fr-ok (the fixture's title)
        "description": "Quand une commande a un avoir, le total affiché ignore la remise.",  # lang: fr-ok
        "fields.department": "accounts payable",
    }
    assert setup.adapters.executor.started == [], "no pod for a decision"


async def test_an_unsure_answer_goes_to_a_person(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    setup.adapters.decisions.answer(route=("billing", 0.55))
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            handle = await _start(setup, temporal_env)
            for _ in range(600):
                if await _state(setup) == "triage":
                    break
                await asyncio.sleep(0.1)
            await handle.terminate("test over")
            await _replay_and_archive(handle, "wi-decision-route-to-a-person-S52-04.json")
    assert await _state(setup) == "triage", "billing, but not sure enough: a person routes it"
    assert len(setup.adapters.decisions.calls) == 1


async def test_an_unusable_answer_is_retried_then_escalated_by_on_fail(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """`invalid_decision` fails the run; the transition's `on_fail` replays it, then escalates."""
    setup.adapters.decisions.answer(route=("sales", 0.97)).answer(route=("sales", 0.97))
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            handle = await _start(setup, temporal_env)
            for _ in range(600):
                if await _state(setup) == "needs_human":
                    break
                await asyncio.sleep(0.1)
            await handle.terminate("test over")
            await _replay_and_archive(handle, "wi-decision-invalid-escalates-S52-04.json")
    assert await _state(setup) == "needs_human"
    assert len(setup.adapters.decisions.calls) == 2, "max_attempts: 2"


async def test_a_refused_decision_escalates_the_work_item_instead_of_killing_it(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """The catalogue entry removed after the workflow was saved: the step refuses (`DecisionRefused`)
    and the work item escalates with the reason, as for a model `prepare_stage` refuses (#543)."""
    from choregos_api.db.models import Event, ModelCatalogueRow
    from choregos_api.db.session import session_scope
    from sqlalchemy import delete, select

    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            await _publish(setup)
            async with session_scope() as session:
                await session.execute(delete(ModelCatalogueRow))
            handle = await _start(setup, temporal_env, publish=False)
            for _ in range(600):
                if await _state(setup) == "needs_human":
                    break
                await asyncio.sleep(0.1)
            await handle.terminate("test over")
            await _replay_and_archive(handle, "wi-decision-refused-escalates-S52-04.json")
    assert await _state(setup) == "needs_human"
    assert setup.adapters.decisions.calls == [], "nothing is asked of a model the catalogue does not enable"
    async with session_scope() as session:
        events = (await session.execute(select(Event))).scalars().all()
    reasons = [str(e.payload.get("reason", "")) for e in events]
    assert any(r.startswith("configuration refused:") and "`decide`" in r for r in reasons), reasons


# ───────────────────────── the in-memory engine ─────────────────────────


async def test_the_local_engine_routes_on_the_answer(setup: Fixture) -> None:
    from choregos_orchestrator.local import run_local

    await _publish(setup)
    setup.adapters.decisions.answer(route=("billing", 0.91))
    trace = await run_local(setup.project_id, setup.work_item_id)
    assert trace.final_state == "billing"
    assert [(s.transition, s.kind) for s in trace.steps] == [("t-route", "decision"), ("t-billing", "system")]


async def test_the_local_engine_escalates_an_unsure_answer_to_a_person(setup: Fixture) -> None:
    from choregos_orchestrator.local import run_local

    await _publish(setup)
    setup.adapters.decisions.answer(route=("billing", 0.6))
    trace = await run_local(setup.project_id, setup.work_item_id, max_steps=3)
    # routed → triage, where a person decides (approved by the simulation) → done.
    assert [s.transition for s in trace.steps] == ["t-route", "t-triage", "t-decide"]
    assert trace.final_state == "done"


async def test_the_local_engine_retries_an_unusable_answer(setup: Fixture) -> None:
    from choregos_orchestrator.local import run_local

    await _publish(setup)
    setup.adapters.decisions.answer(route=("sales", 0.9)).answer(route=("billing", 0.9))
    trace = await run_local(setup.project_id, setup.work_item_id)
    assert [s.next_state for s in trace.steps] == ["inbox", "routed", "billing"]
    assert trace.final_state == "billing"
