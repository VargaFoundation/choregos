"""The interpreter chooses the next step from what the agent answered (S48-01, ADR 0106).

"Develop it, then answer: is it finished? how sure are you?" — the agent loops until it says it is
done, and a low confidence hands the work item to a person. Before, the interpreter took the first
transition declared from the checkpoint whatever the agent answered. Real interpreter, fakes,
Temporal's test server; the histories replay and are archived for the next changes.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from typing import Any

import pytest

from .conftest import Fixture, recording_history, stage_result

pytestmark = pytest.mark.asyncio

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"

DEVELOP = """apiVersion: choregos/v1
kind: Workflow
metadata: { name: develop-until-done, version: 1 }
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
  needs_human: { display: Needs a human, kind: wait }
  abandoned: { display: Abandoned, terminal: true }
transitions:
  - { id: t-develop, from: inbox, to: checkpoint, by: developer, outputs: [done, confidence] }
  - { id: t-again, from: checkpoint, to: inbox, by: platform, when: { done: false } }
  - { id: t-unsure, from: checkpoint, to: unsure, by: platform, when: { confidence: { lt: 0.6 } } }
  - { id: t-finish, from: checkpoint, to: finished, by: platform }
  - { id: t-decide, from: unsure, to: finished, by: lead }
  - { id: t-abandon, from: needs_human, to: abandoned, by: lead }
defaults:
  from_any_agent_state: { on_question: needs_human }
  needs_human: { on_answer: resume, on_abandon: abandoned }
"""


def _answers(*answers: dict[str, Any]) -> tuple[Any, list[dict[str, Any]]]:
    """The developer's answers, one per pass, in order; the list records what was asked."""
    asked: list[dict[str, Any]] = []

    def play(stage_input: Any) -> Any:
        assert stage_input.transition.id == "t-develop", stage_input.transition.id
        outputs = answers[len(asked)]
        asked.append(outputs)
        return stage_result(f"pass {len(asked)}", outputs=outputs)

    return play, asked


async def _start(setup: Fixture, temporal_env: Any) -> Any:
    from choregos_api.db.models import Project, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.rbac import SYSTEM
    from choregos_api.services.definitions import publier_workflow
    from choregos_api.temporal import interpreter_id

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        definition = await publier_workflow(session, SYSTEM, project, DEVELOP)
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.workflow_def_id = definition.id
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


async def test_the_agent_loops_until_it_answers_done(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    play, asked = _answers(
        {"done": False, "confidence": 0.9},
        {"done": "false", "confidence": "0.85"},  # JSON written loosely still chooses
        {"done": True, "confidence": 0.9},
    )
    setup.adapters.executor.handler = play
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            handle = await _start(setup, temporal_env)
            outcome = await asyncio.wait_for(handle.result(), timeout=60)
            await _replay_and_archive(handle, "wi-branch-on-outputs-loop-S48-01.json")
    assert outcome["state"] == "finished"
    assert len(asked) == 3, "two passes said not done, the third finished"


async def test_a_low_confidence_hands_the_work_item_to_a_person(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    play, asked = _answers({"done": True, "confidence": 0.3})
    setup.adapters.executor.handler = play
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            handle = await _start(setup, temporal_env)
            for _ in range(600):
                async with session_scope() as session:
                    item = await session.get(WorkItem, setup.work_item_id)
                    assert item is not None
                    if item.state == "unsure":
                        break
                await asyncio.sleep(0.1)
            await handle.terminate("test over")
            await _replay_and_archive(handle, "wi-branch-on-outputs-unsure-S48-01.json")
    assert item.state == "unsure", "done, but not sure enough: a person decides"
    assert len(asked) == 1


async def test_a_restarted_interpreter_reads_the_last_answer_back(setup: Fixture) -> None:
    """An interpreter started again from the database (a dead worker, S22-41) still chooses on the
    last answer: `load_context` reads the outputs of the work item's last finished run."""
    from datetime import timedelta

    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow
    from choregos_orchestrator.activities.interpretation import load_context

    now = utcnow()
    async with session_scope(orgs="*") as session:
        for attempt, (ended, outputs) in enumerate(
            [
                (now - timedelta(minutes=10), {"done": False, "confidence": 0.9}),
                (now - timedelta(minutes=1), {"done": True, "confidence": 0.4, "note": None}),
            ],
            start=1,
        ):
            session.add(
                Run(
                    work_item_id=setup.work_item_id,
                    project_id=setup.project_id,
                    transition_id="t-develop",
                    stage_role="implement",
                    attempt=attempt,
                    status="succeeded",
                    ended_at=ended,
                    result={"status": "done", "summary": "pass", "outputs": outputs},
                )
            )
        # A run still going has no answer yet: it is not the last answer.
        session.add(
            Run(
                work_item_id=setup.work_item_id,
                project_id=setup.project_id,
                transition_id="t-develop",
                stage_role="implement",
                attempt=3,
                status="running",
            )
        )

    context = await load_context({"project_id": setup.project_id, "work_item_id": setup.work_item_id})
    assert context["last_outputs"] == {"done": True, "confidence": 0.4}
