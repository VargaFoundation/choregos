"""The decision ledger (ADR 0121 §6, S52-05): every answered question, the branch it took, and what was
observed afterwards — a person's verdict, the work item's end, its return.

The activities that write it on the fakes, the real interpreter on Temporal's test server (its
histories replay and are archived), and the in-memory engine.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy import select

from .conftest import Fixture, stage_result
from .test_decision_branch import ROUTE, _publish, _replay_and_archive, _start, _state

pytestmark = pytest.mark.asyncio

#: An agent asked for an answer and its confidence (ADR 0106), and a declined end.
DEVELOP = """apiVersion: choregos/v1
kind: Workflow
metadata: { name: develop-and-answer, version: 3 }
initial: inbox
actors:
  developer: { type: agent, role: implement, model: "profile:standard" }
  platform: { type: system }
  lead: { type: human, group: maintainers, sla_hours: 8 }
states:
  inbox: { display: To do }
  checkpoint: { display: Checkpoint }
  unsure: { display: Unsure, kind: wait }
  finished: { display: Finished, terminal: true }
  refused: { display: Refused, terminal: true, outcome: declined }
transitions:
  - { id: t-develop, from: inbox, to: checkpoint, by: developer, outputs: [done, confidence] }
  - { id: t-unsure, from: checkpoint, to: unsure, by: platform, when: { confidence: { lt: 0.6 } } }
  - { id: t-finish, from: checkpoint, to: finished, by: platform }
  - { id: t-decide, from: unsure, to: finished, by: lead }
  - { id: t-refuse, from: unsure, to: refused, by: lead }
"""


async def _records() -> list[Any]:
    from choregos_api.db.models import DecisionRecordRow
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        rows = await session.execute(select(DecisionRecordRow).order_by(DecisionRecordRow.question))
        return list(rows.scalars().all())


async def _pin(setup: Fixture, yaml: str) -> None:
    from choregos_api.db.models import Project, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.rbac import SYSTEM
    from choregos_api.services.definitions import publier_workflow

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        definition = await publier_workflow(session, SYSTEM, project, yaml)
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.workflow_def_id = definition.id


async def _set_state(setup: Fixture, state: str) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.state = state


def _decide_payload(setup: Fixture, attempt: int = 1) -> dict[str, Any]:
    import yaml
    from choregos_contracts import Workflow
    from choregos_core import WorkflowEngine
    from choregos_orchestrator.activities.decision import decision_request

    engine = WorkflowEngine(Workflow.model_validate(yaml.safe_load(ROUTE)))
    transition = next(t for t in engine.wf.transitions if t.key == "t-route")
    actor = engine.decision_of(transition)
    assert actor is not None
    return decision_request(
        project_id=setup.project_id,
        work_item_id=setup.work_item_id,
        transition=transition,
        decision=actor,
        attempt=attempt,
        last_outputs=None,
    )


async def _human_request(setup: Fixture, transition_id: str, kind: str = "approval") -> str:
    from choregos_orchestrator.activities.tracker import create_human_request

    created = await create_human_request(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "transition_id": transition_id,
            "kind": kind,
            "payload": {"summary": "decide"},
        }
    )
    return str(created["request_id"])


async def _close(request_id: str, **decision: Any) -> None:
    from choregos_orchestrator.activities.tracker import close_human_request

    await close_human_request({"request_id": request_id, "decided_by": "lead", "decision": decision})


# ───────────────────────── the records ─────────────────────────


async def test_a_decision_step_records_each_answer_once(setup: Fixture) -> None:
    from choregos_orchestrator.activities.decision import decide

    await _publish(setup)
    setup.adapters.decisions.answer(route=("billing", 0.93), urgent=(False, 0.8))
    decided = await decide(_decide_payload(setup))
    route, urgent = await _records()
    assert (route.question, route.answer, route.probability) == ("route", "billing", 0.93)
    assert (urgent.question, urgent.answer, urgent.probability) == ("urgent", False, 0.8)
    assert route.run_id == decided["run_id"] and route.source == "decision_model"
    assert route.model == "platform/decide"
    assert route.model_version == "fake-decide", "the model the gateway says answered behind the alias"
    assert (route.workflow, route.workflow_version) == ("route-by-decision", 1)
    assert (route.step, route.transition_id) == ("inbox", "t-route")
    assert route.work_item_key == setup.tracker_key and route.project_slug == setup.project_slug
    assert route.transition is None and route.overridden is None and route.terminal is None
    assert route.reopened is False

    await decide(_decide_payload(setup))  # a retried activity reads the result back
    assert len(await _records()) == 2, "a replayed decision records nothing more"


async def test_a_decision_that_failed_records_nothing(setup: Fixture) -> None:
    from choregos_orchestrator.activities.decision import decide

    await _publish(setup)
    setup.adapters.decisions.answer(route=("sales", 0.97))  # not an option offered
    decided = await decide(_decide_payload(setup))
    assert decided["result"]["reason"] == "invalid_decision"
    assert await _records() == []


async def test_the_ledger_insert_ignores_a_question_already_recorded(setup: Fixture) -> None:
    """`ON CONFLICT DO NOTHING` on `(run_id, question)`: the second write of a run adds only what is new."""
    from choregos_api.db.session import session_scope
    from choregos_api.services import DecidedBy, record_decisions
    from choregos_core import utcnow
    from choregos_core.decision_ledger import Answered
    from choregos_orchestrator.activities.decision import decide

    await _publish(setup)
    decided = await decide(_decide_payload(setup))
    (first,) = [r for r in await _records() if r.question == "route"]
    async with session_scope() as session:
        by = DecidedBy(
            org_id=first.org_id,
            project_id=first.project_id,
            project_slug=first.project_slug,
            work_item_id=first.work_item_id,
            work_item_key=first.work_item_key,
            run_id=decided["run_id"],
            workflow=first.workflow,
            workflow_version=1,
            step="inbox",
            transition_id="t-route",
            source="decision_model",
            model="platform/decide",
            model_version=None,
            decided_at=utcnow(),
        )
        added = await record_decisions(
            session, by, [Answered("route", "tech", 0.5), Answered("priority", "high", 0.7)]
        )
    assert added == 1
    rows = {r.question: r for r in await _records()}
    assert set(rows) == {"priority", "route", "urgent"}
    assert rows["route"].answer == "billing", "the first record stands"


async def _agent_run(setup: Fixture, outputs: dict[str, Any], *, backend: str = "claude-code") -> str:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    async with session_scope() as session:
        session.add(
            Run(
                id=f"{setup.work_item_id}-t-develop-1",
                work_item_id=setup.work_item_id,
                project_id=setup.project_id,
                transition_id="t-develop",
                stage_role="implement",
                status="running",
                backend=backend,
                model="anthropic/claude-sonnet",
                agent_slug="developer",
                agent_version=4,
                ended_at=utcnow(),
                stage_input={
                    "transition": {
                        "id": "t-develop",
                        "role": "implement",
                        "from": "inbox",
                        "to": "checkpoint",
                        "outputs": list(outputs),
                    }
                },
            )
        )
    return f"{setup.work_item_id}-t-develop-1"


async def test_an_agent_s_declared_confidence_is_recorded_beside_its_answer(setup: Fixture) -> None:
    from choregos_orchestrator.activities.bilan import record_run_outcome

    await _pin(setup, DEVELOP)
    run_id = await _agent_run(setup, {"done": True, "confidence": 0.85})
    result = stage_result("done", outputs={"done": True, "confidence": 0.85}).model_dump(mode="json")
    await record_run_outcome({"project_id": setup.project_id, "run_id": run_id, "result": result})
    await record_run_outcome({"project_id": setup.project_id, "run_id": run_id, "result": result})
    (done,) = await _records()
    assert (done.question, done.answer, done.probability, done.source) == ("done", True, 0.85, "agent")
    assert (done.model, done.model_version) == ("anthropic/claude-sonnet", "developer@4")
    assert (done.workflow, done.workflow_version, done.step) == ("develop-and-answer", 3, "inbox")


async def test_a_job_and_an_unfinished_stage_record_no_confidence(setup: Fixture) -> None:
    from choregos_contracts import StageResult, StageStatus
    from choregos_orchestrator.activities.bilan import record_run_outcome

    await _pin(setup, DEVELOP)
    run_id = await _agent_run(setup, {"done": True, "confidence": 0.85}, backend="job")
    result = stage_result("done", outputs={"done": True, "confidence": 0.85}).model_dump(mode="json")
    await record_run_outcome({"project_id": setup.project_id, "run_id": run_id, "result": result})
    assert await _records() == [], "a job's figures are measured, not a confidence it declares"

    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        run = await session.get(Run, run_id)
        assert run is not None
        run.backend, run.result = "claude-code", None
    failed = StageResult.model_validate(
        {"status": StageStatus.FAILED, "summary": "broke", "outputs": {"done": True, "confidence": 0.9}}
    ).model_dump(mode="json")
    await record_run_outcome({"project_id": setup.project_id, "run_id": run_id, "result": failed})
    assert await _records() == [], "a failed stage answered nothing"


async def test_the_transition_taken_is_written_once(setup: Fixture) -> None:
    from choregos_orchestrator.activities.decision import decide, note_transition_taken

    await _publish(setup)
    decided = await decide(_decide_payload(setup))
    noted = await note_transition_taken({"run_id": decided["run_id"], "transition": "billing"})
    again = await note_transition_taken({"run_id": decided["run_id"], "transition": "triage"})
    assert (noted, again) == ({"noted": 2}, {"noted": 0})
    assert {r.transition for r in await _records()} == {"billing"}


# ───────────────────────── outcomes observed after the fact ─────────────────────────


async def test_the_first_human_step_after_a_decision_judges_it(setup: Fixture) -> None:
    from choregos_orchestrator.activities.decision import decide

    await _publish(setup)
    await decide(_decide_payload(setup))
    rejected = await _human_request(setup, "t-decide")
    await _close(rejected, approved=False, kind="approval", reason="the wrong team")
    assert {r.overridden for r in await _records()} == {True}, "sent back: the decision is overridden"

    approved = await _human_request(setup, "t-give-up")
    await _close(approved, approved=True, kind="approval")
    assert {r.overridden for r in await _records()} == {True}, "a later verdict changes nothing"
    assert all(r.observed_at is not None for r in await _records())


async def test_an_approval_confirms_a_decision(setup: Fixture) -> None:
    from choregos_orchestrator.activities.decision import decide

    await _publish(setup)
    await decide(_decide_payload(setup))
    await _close(await _human_request(setup, "t-decide"), approved=True, kind="approval")
    assert {r.overridden for r in await _records()} == {False}


@pytest.mark.parametrize(
    ("transition_id", "kind", "decision"),
    [
        ("t-route", "approval", {"approved": False}),  # an escalation of the decision step itself
        ("t-decide", "question", {"approved": False, "answer": "tech"}),  # an answer judges nothing
        ("t-decide", "approval", {"approved": False, "superseded": "the work item resumed"}),
        ("t-billing", "approval", {"approved": False}),  # a system transition: no person's step
    ],
)
async def test_what_is_not_a_human_step_judges_nothing(
    setup: Fixture, transition_id: str, kind: str, decision: dict[str, Any]
) -> None:
    from choregos_orchestrator.activities.decision import decide

    await _publish(setup)
    await decide(_decide_payload(setup))
    await _close(await _human_request(setup, transition_id, kind), **decision)
    assert {r.overridden for r in await _records()} == {None}


async def test_a_human_step_asked_before_the_decision_does_not_judge_it(setup: Fixture) -> None:
    from choregos_orchestrator.activities.decision import decide

    await _publish(setup)
    earlier = await _human_request(setup, "t-decide")
    await asyncio.sleep(0.01)
    await decide(_decide_payload(setup))
    await _close(earlier, approved=False, kind="approval")
    assert {r.overridden for r in await _records()} == {None}


@pytest.mark.parametrize(
    ("state", "terminal"), [("billing", "success"), ("triage", "cancelled"), ("inbox", "cancelled")]
)
async def test_the_end_of_the_work_item_is_its_decisions_terminal_outcome(
    setup: Fixture, state: str, terminal: str
) -> None:
    from choregos_orchestrator.activities.decision import decide
    from choregos_orchestrator.activities.tracker import close_out

    await _publish(setup)
    await decide(_decide_payload(setup))
    await _set_state(setup, state)
    await close_out({"project_id": setup.project_id, "work_item_id": setup.work_item_id})
    await close_out({"project_id": setup.project_id, "work_item_id": setup.work_item_id})
    assert {r.terminal for r in await _records()} == {terminal}


async def test_a_declined_end_is_recorded_as_declined(setup: Fixture) -> None:
    from choregos_orchestrator.activities.bilan import record_run_outcome
    from choregos_orchestrator.activities.tracker import close_out

    await _pin(setup, DEVELOP)
    run_id = await _agent_run(setup, {"done": True, "confidence": 0.85})
    result = stage_result("done", outputs={"done": True, "confidence": 0.85}).model_dump(mode="json")
    await record_run_outcome({"project_id": setup.project_id, "run_id": run_id, "result": result})
    await _set_state(setup, "refused")
    await close_out({"project_id": setup.project_id, "work_item_id": setup.work_item_id})
    assert {r.terminal for r in await _records()} == {"declined"}


# ───────────────────────── the interpreter ─────────────────────────


async def test_the_interpreter_records_the_branch_a_sure_decision_took(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    setup.adapters.decisions.answer(route=("billing", 0.93), urgent=(False, 0.8))
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            handle = await _start(setup, temporal_env)
            outcome = await asyncio.wait_for(handle.result(), timeout=60)
            await _replay_and_archive(handle, "wi-decision-ledger-billing-S52-05.json")
    assert outcome["state"] == "billing"
    records = await _records()
    assert [(r.question, r.transition, r.terminal) for r in records] == [
        ("route", "billing", "success"),
        ("urgent", "billing", "success"),
    ]


async def test_a_person_who_sends_back_an_unsure_decision_overrides_it(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope

    setup.adapters.decisions.answer(route=("billing", 0.55))
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            handle = await _start(setup, temporal_env)
            request: Any = None
            for _ in range(600):
                async with session_scope() as session:
                    request = (
                        await session.execute(select(HumanRequest).where(HumanRequest.decided_at.is_(None)))
                    ).scalar_one_or_none()
                if request is not None:
                    break
                await asyncio.sleep(0.1)
            assert request is not None and request.transition_id == "t-decide"
            await handle.signal(
                "human_decision",
                {"request_id": request.id, "kind": "approval", "approved": False, "decided_by": "lead"},
            )
            for _ in range(600):
                if {r.overridden for r in await _records()} == {True}:
                    break
                await asyncio.sleep(0.1)
            await handle.terminate("test over")
            await _replay_and_archive(handle, "wi-decision-ledger-overridden-S52-05.json")
    assert await _state(setup) == "triage"
    records = await _records()
    assert [(r.question, r.transition, r.overridden) for r in records] == [
        ("route", "triage", True),
        ("urgent", "triage", True),
    ], "unsure, it went to a person, who sent it back"


async def test_the_interpreter_records_an_agent_s_confidence_and_where_it_led(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    from choregos_api.temporal import interpreter_id

    await _pin(setup, DEVELOP)
    setup.adapters.executor.handler = lambda _stage: stage_result(
        "done", outputs={"done": True, "confidence": 0.9}
    )
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            handle = await temporal_env.client.start_workflow(
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
            outcome = await asyncio.wait_for(handle.result(), timeout=60)
            await _replay_and_archive(handle, "wi-decision-ledger-agent-S52-05.json")
    assert outcome["state"] == "finished"
    (done,) = await _records()
    assert (done.question, done.source, done.transition, done.terminal) == (
        "done",
        "agent",
        "finished",
        "success",
    )


# ───────────────────────── the in-memory engine ─────────────────────────


async def test_the_local_engine_feeds_the_ledger_as_the_interpreter_does(setup: Fixture) -> None:
    from choregos_orchestrator.local import run_local

    await _publish(setup)
    setup.adapters.decisions.answer(route=("billing", 0.6))
    trace = await run_local(setup.project_id, setup.work_item_id)
    assert trace.final_state == "done"
    records = await _records()
    assert [(r.question, r.transition, r.overridden, r.terminal) for r in records] == [
        ("route", "triage", False, "success"),
        ("urgent", "triage", False, "success"),
    ], "unsure → a person, who approved (the simulation's default) → done"
