"""A parked ticket (escalated, or stopped by a question) RESUMES (#313).

The contract declared `needs_human.on_answer: resume` and nothing played it: on the dev tenant,
three escalated tickets had no way out but abandonment, and "Replay the stage" (the
`rerun_stage` gesture) was accepted by the API and then ignored by the interpreter.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
from choregos_contracts import StageResult, StageStatus

from .conftest import Fixture, recording_history, refine_result
from .kits.escalation import requests_of
from .test_interpreter import _runs, _ticket, _wait_state, _wait_until, scripted, start

pytestmark = pytest.mark.asyncio

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"
FAILURE = StageResult(status=StageStatus.FAILED, summary="the clone failed", reason="agent_error")


async def test_replay_the_stage_resumes_an_escalated_ticket(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """Two failures exhaust `t-refine` and the work item escalates; "Replay the stage" brings it
    back for ONE more attempt (S27-13): it succeeds and the spec arrives."""
    scripted(setup.adapters, FAILURE, FAILURE, refine_result("spec"))
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "needs_human")
        await _wait_until(handle, "abandon is offered", lambda s: s["pending_request"])
        assert await _runs(setup) == 2

        await handle.signal("control", {"action": "rerun_stage", "by": "vincent"})
        await _wait_state(handle, "awaiting_spec_approval")
        status = await handle.query("status")
        assert await _runs(setup) == 3
        # Attempt numbers do not restart at 1: they make the run identifier.
        assert status["attempts"]["t-refine"] == 3, status["attempts"]
        await handle.signal("control", {"action": "stop"})
        await handle.result()

    abandons = [d for d in await requests_of(setup) if d.transition_id == "t-abandon"]
    assert abandons, "the escalation offered abandonment"
    assert all(d.decided_at is not None for d in abandons), "the proposed abandonment is closed by the resume"


async def test_a_rerun_buys_one_attempt_not_a_new_allowance(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """S27-13: `max_attempts` is 2. Each rerun is ONE more attempt: a rerun whose attempt fails
    escalates again at once (before: it had two fresh attempts, and the third failure of the
    script would have been played), and every click pays for exactly one run."""
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    scripted(setup.adapters, FAILURE, FAILURE, FAILURE, FAILURE, FAILURE, refine_result("spec"))
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "needs_human")
        await _wait_until(handle, "abandon is offered", lambda s: s["pending_request"])
        assert await _runs(setup) == 2
        for expected in (3, 4):
            await handle.signal("control", {"action": "rerun_stage", "by": "vincent"})
            await _wait_until(
                handle, "the rerun is played", lambda s, n=expected: s["attempts"]["t-refine"] == n
            )
            await _wait_state(handle, "needs_human")
            await _wait_until(handle, "the new abandon is proposed", lambda s: s["pending_request"])
            assert await _runs(setup) == expected, "a rerun plays one run, not a new allowance"
        status = await handle.query("status")
        assert status["state"] == "needs_human", status
        await handle.signal("control", {"action": "stop"})
        await handle.result()
        history = await handle.fetch_history()
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        target = HISTORIES / "wi-rerun-one-attempt-S27-13.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_an_answer_buys_one_attempt_not_a_new_allowance(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """S27-04: `max_attempts` is 2. A failure, then a question on the second attempt; the answer is
    ONE more attempt: it fails and the work item escalates after three runs (before: the answer
    reset the attempts, and a fourth run was played)."""
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    question = StageResult(
        status=StageStatus.NEEDS_HUMAN,
        summary="which currency?",
        reason="question",
        questions=[{"text": "Which currency for credit notes?", "options": ["EUR", "order currency"]}],
    )
    scripted(setup.adapters, FAILURE, question, FAILURE, FAILURE, refine_result("spec"))
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "needs_human")
        await _wait_until(handle, "the question is asked", lambda s: s["pending_request"])
        assert await _runs(setup) == 2
        asked = next(d for d in await requests_of(setup) if d.kind == "question")
        await handle.signal(
            "human_decision",
            {
                "request_id": asked.id,
                "kind": "question",
                "approved": False,
                "answer": "the order currency",
                "decided_by": "vincent",
            },
        )
        await _wait_until(handle, "the answer is played", lambda s: s["attempts"]["t-refine"] == 3)
        await _wait_state(handle, "needs_human")
        await _wait_until(handle, "the abandon is offered", lambda s: s["pending_request"])
        assert await _runs(setup) == 3, "an answer plays one run, not a new allowance"
        await handle.signal("control", {"action": "stop"})
        await handle.result()
        history = await handle.fetch_history()
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        target = HISTORIES / "wi-answer-one-attempt-S27-04.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_an_answer_resumes_the_step_that_asked_the_question(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """`on_answer: resume`: the answer brings the ticket back to the step, and the resuming agent READS it."""
    question = StageResult(
        status=StageStatus.NEEDS_HUMAN,
        summary="which currency?",
        reason="question",
        questions=[{"text": "Which currency for credit notes?", "options": ["EUR", "order currency"]}],
    )
    scripted(setup.adapters, question, refine_result("spec"))
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "needs_human")
        await _wait_until(handle, "abandon is offered", lambda s: s["pending_request"])
        asked = next(d for d in await requests_of(setup) if d.kind == "question")

        await handle.signal(
            "human_decision",
            {
                "request_id": asked.id,
                "kind": "question",
                "approved": False,
                "answer": "the order currency",
                "decided_by": "vincent",
            },
        )
        await _wait_state(handle, "awaiting_spec_approval")
        await handle.signal("control", {"action": "stop"})
        await handle.result()

    resumed = setup.adapters.executor.started[-1].stage_input
    assert resumed is not None and resumed.playbook.prompt is not None
    assert "Which currency for credit notes?" in resumed.playbook.prompt
    assert "the order currency" in resumed.playbook.prompt
    # The answer was used: the next step will not read it again.
    assert "human_answer" not in ((await _ticket(setup)).documents or {})
    # The question and the proposed abandonment are closed. The approval of the new spec may be waiting,
    # or not exist yet: the state is read before its request is created (red in CI, #317).
    open_requests = {d.transition_id for d in await requests_of(setup) if d.decided_at is None}
    assert open_requests <= {"t-approve-spec"}, open_requests


async def test_replay_outside_a_parked_ticket_is_forgotten(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """On a ticket waiting for an ordinary approval, `rerun_stage` moves nothing, and does not
    stay in reserve for an escalation to come."""
    scripted(setup.adapters, refine_result("spec"))
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "awaiting_spec_approval")
        await handle.signal("control", {"action": "rerun_stage"})
        await handle.signal("control", {"action": "pause"})
        await _wait_until(handle, "paused", lambda s: s["paused"])
        status = await handle.query("status")
        assert status["state"] == "awaiting_spec_approval"
        assert await _runs(setup) == 1
        await handle.signal("control", {"action": "stop"})
        await handle.result()


async def test_a_rerun_that_cannot_play_does_not_loop(
    setup: Fixture, temporal_env: Any, worker_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A history from before the marker: `patched` answered no there, and the SDK remembers the
    answer: `rerun_stage` cannot play. It must be forgotten. On the dev tenant (10/09), it stayed
    in place, and the ticket recreated an abandonment request every 4 s."""
    from choregos_orchestrator.workflows import interpreter

    real_patched = interpreter.workflow.patched
    monkeypatch.setattr(
        interpreter.workflow,
        "patched",
        lambda marker: False if marker == interpreter.RESUME_AFTER_ESCALATION else real_patched(marker),
    )
    scripted(setup.adapters, FAILURE, FAILURE)
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "needs_human")
        await _wait_until(handle, "abandon is offered", lambda s: s["pending_request"])
        await handle.signal("control", {"action": "rerun_stage"})
        import asyncio

        # No pause here: it stopped the loop on the first turn, and hid the defect. A wait that
        # loops shows in its history, which grows with every request recreated; a wait that
        # waits does not move.
        await asyncio.sleep(2)
        before = len((await handle.fetch_history()).events)
        await asyncio.sleep(3)
        after = len((await handle.fetch_history()).events)
        assert after == before, f"the rerun made the human wait loop ({before} -> {after} events)"
        assert (await handle.query("status"))["state"] == "needs_human"
        await handle.signal("control", {"action": "stop"})
        await handle.result()
