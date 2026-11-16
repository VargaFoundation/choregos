"""`WorkflowInterpreter`: full traversal, human wait, resumes, control."""

from __future__ import annotations

from typing import Any

import pytest
from choregos_contracts import StageResult, StageStatus

from .conftest import Fixture, refine_result, stage_result
from .kits.workers import worker_with

pytestmark = pytest.mark.asyncio


def scripted(adapters: Any, *results: StageResult) -> None:
    for result in results:
        adapters.executor.queue_result(result)


async def start(env: Any, setup: Fixture, task_queue: str = "test") -> Any:
    return await env.client.start_workflow(
        "WorkflowInterpreter",
        {
            "project_id": setup.project_id,
            "project_slug": setup.project_slug,
            "work_item_id": setup.work_item_id,
            "tracker_key": setup.tracker_key,
        },
        id=f"wi-{setup.project_slug}-123",
        task_queue=task_queue,
    )


async def test_default_simple_full_traversal(setup: Fixture, temporal_env: Any, worker_factory: Any) -> None:
    """A ticket goes through `default-simple` from end to end: agents, human, system, train."""
    scripted(
        setup.adapters,
        stage_result("spec written", outputs={"size": "M", "risk": "low", "spec_markdown": "## Spec"}),
        stage_result(
            "implemented", artifacts={"branch": "choregos/123", "commits": ["fix(orders): credit notes"]}
        ),
        stage_result("verified"),
    )
    setup.adapters.cd.set_health("billing-api", "Healthy")

    async with worker_factory():
        handle = await start(temporal_env, setup)

        # 1. the `refine` agent ran, the human validation is requested
        await _wait_state(handle, "awaiting_spec_approval")
        await handle.signal(
            "human_decision", {"approved": True, "decided_by": "augustin", "kind": "approval"}
        )

        # 2. implementation, verification, PR: CI and review arrive by events
        await _wait_state(handle, "pr_open")
        await handle.signal(
            "inbound",
            {
                "type": "scm.check.completed",
                "source": "github",
                "delivery_id": "d1",
                "payload": {"conclusion": "success"},
            },
        )
        setup.adapters.scm.set_checks(_pr_ref(setup), "success")
        setup.adapters.scm.submit_review(_pr_ref(setup), "marie", "approved")
        await handle.signal(
            "inbound",
            {
                "type": "scm.pr.review_submitted",
                "source": "github",
                "delivery_id": "d2",
                "payload": {"state": "approved"},
            },
        )

        # 3. the train announces the successful deployment
        await _wait_state(handle, "merged")
        await handle.signal(
            "inbound",
            {
                "type": "cd.rollout.completed",
                "source": "argocd",
                "delivery_id": "d3",
                "payload": {"env": "prod"},
            },
        )
        outcome = await handle.result()

    assert outcome["state"] == "deployed_prod"
    assert outcome["cost_usd"] >= 0
    comment = setup.adapters.tracker.status_comment(setup.tracker_key)
    assert comment is not None and "Choregos — progress" in comment
    assert "agent refine" in comment and "agent implement" in comment


async def _runs(setup: Fixture) -> int:
    """How many agent steps ran for this ticket."""
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from sqlalchemy import func, select

    async with session_scope() as session:
        return int(
            (
                await session.execute(
                    select(func.count()).select_from(Run).where(Run.work_item_id == setup.work_item_id)
                )
            ).scalar_one()
        )


async def test_human_rejection_returns_to_refining(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """A refusal sends the ticket back to make a SECOND spec, and it goes through the human gate again.

    This test is called `returns_to_refining` and never observed `refining`: it waited for
    `awaiting_spec_approval`, then RE-QUERIED the state and asserted it. Two calls, and the state
    can move between the two: it went red in a group run on 2026-09-28 on
    `assert 'refining' == 'awaiting_spec_approval'`, because the wait had recognised the FIRST
    pass through the gate, before the refusal was handled.

    `refining` is a PASSING state: polling it is the same illusion as the one already fixed
    in `_wait_until` and in `test_pause_and_resume`. What can be observed without a race is its
    effect: a second agent step ran.
    """
    scripted(setup.adapters, refine_result("spec v1"), refine_result("spec v2"))
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "awaiting_spec_approval")
        after_the_first = await _runs(setup)
        assert after_the_first == 1, after_the_first

        await handle.signal(
            "human_decision",
            {
                "approved": False,
                "decided_by": "augustin",
                "kind": "approval",
                "reason": "scope too wide",
            },
        )
        # The effect of the refusal, not the state at one instant: a SECOND agent step runs.
        import asyncio

        for _ in range(600):
            if await _runs(setup) > after_the_first:
                break
            await asyncio.sleep(0.1)
        assert await _runs(setup) == 2, "the refusal did not send the ticket back to make a second spec"

        # And it does come back to wait for a human, with the second spec.
        await _wait_state(handle, "awaiting_spec_approval")
        await handle.signal("control", {"action": "stop"})
        await handle.result()


async def test_failures_are_bounded_then_escalate(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """Two attempts, then escalation to `needs_human`: never an infinite loop."""
    failure = StageResult(status=StageStatus.FAILED, summary="the agent crashed", reason="agent_error")
    scripted(setup.adapters, failure, failure, failure)
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "needs_human")
        status = await handle.query("status")
        assert status["attempts"]["t-refine"] == 2, status["attempts"]
        await handle.signal("control", {"action": "stop"})
        await handle.result()


async def test_agent_question_creates_human_request(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    scripted(
        setup.adapters,
        StageResult(
            status=StageStatus.NEEDS_HUMAN,
            summary="which currency for the credit notes?",
            reason="question",
            questions=[{"text": "Which currency?", "options": ["EUR", "order currency"]}],
        ),
    )
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "needs_human")
        await handle.signal("control", {"action": "stop"})
        await handle.result()

    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (await session.execute(select(HumanRequest))).scalars().all()
    assert any(row.kind == "question" for row in rows)
    comments = setup.adapters.tracker.items[setup.tracker_key].comments
    assert any("Choregos — question" in c.body for c in comments)


async def test_pause_and_resume(setup: Fixture, temporal_env: Any, worker_factory: Any) -> None:
    scripted(setup.adapters, refine_result("spec"), stage_result("implemented"), stage_result("verified"))
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "awaiting_spec_approval")
        await handle.signal("control", {"action": "pause"})
        await handle.signal("human_decision", {"approved": True, "decided_by": "a", "kind": "approval"})
        status = await handle.query("status")
        assert status["paused"] is True
        await handle.signal("control", {"action": "resume"})
        # What we want to prove is that the resume UNBLOCKS: no longer paused, and the workflow
        # has left the human wait. Waiting for `in_progress` by equality waited for a passing
        # state, which the polling misses as soon as the machine is fast.
        await _wait_until(
            handle,
            "the workflow resumed",
            lambda s: not s["paused"] and s["state"] != "awaiting_spec_approval",
            timeout=40,
        )
        await handle.signal("control", {"action": "stop"})
        await handle.result()


async def test_cost_is_counted_once_per_run(setup: Fixture, temporal_env: Any, worker_factory: Any) -> None:
    """The cost comes from the gateway, once per run: no double counting."""
    scripted(setup.adapters, refine_result("spec"))
    gateway = setup.adapters.gateway
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "awaiting_spec_approval")
        key_id = next(iter(gateway.keys), None) or next(iter(gateway.spends))
        gateway.spends[key_id] = _spend()
        await handle.signal("control", {"action": "stop"})
        await handle.result()

    from choregos_api.db.models import CostLedger, Run
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        runs = (await session.execute(select(Run))).scalars().all()
        ledger = (await session.execute(select(CostLedger))).scalars().all()
    assert len(runs) == 1
    assert len(ledger) == 1, "a single cost line per run"


def _spend() -> Any:
    from choregos_core import Spend

    return Spend(tokens_in=41000, tokens_out=3000, tokens_cached=28000, cost_usd=0.34, requests=12)


def _pr_ref(setup: Fixture) -> Any:
    from choregos_core.domain import PrRef

    prs = list(setup.adapters.scm.prs.values())
    if prs:
        return prs[0].ref
    return PrRef(repo="varga/billing-api", number=1)


async def _wait_until(handle: Any, description: str, predicate: Any, *, timeout: int = 60) -> None:
    """Waits for a CONDITION to be true on the workflow `status`.

    Waiting for a state by equality only works for a **stable** state: one where the workflow
    stops (a human wait, an end). For a passing state, 100 ms polling misses it
    one time in two: `test_pause_and_resume` waited for `in_progress` after a resume and went
    red in CI on `pr_open`, because the following steps had already passed. A test that
    fails depending on the machine load says nothing about the code.
    """
    import asyncio
    import time

    deadline = time.monotonic() + timeout
    status: Any = None
    while time.monotonic() < deadline:
        status = await _query_status(handle)
        if status is not None and predicate(status):
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"{description}: never true (last status: {status})")


async def _query_status(handle: Any) -> Any:
    """One `status` query, or None when it outlasted its deadline (#492, #496).

    On a loaded machine the time-skipping test server can reject a workflow-task completion
    ("invalid history builder state for action"); until the task is retried, a query hangs. The
    server then answers DEADLINE_EXCEEDED, and our own `rpc_timeout` ends it as CANCELLED
    ("Timeout expired"). That says nothing about the workflow, so the query gets a short deadline
    and the caller asks again until its own timeout.
    """
    from datetime import timedelta

    from temporalio.service import RPCError, RPCStatusCode

    try:
        return await handle.query("status", rpc_timeout=timedelta(seconds=10))
    except RPCError as error:
        if error.status not in (RPCStatusCode.DEADLINE_EXCEEDED, RPCStatusCode.CANCELLED):
            raise
        return None


async def _wait_state(handle: Any, state: str, *, timeout: int = 60) -> None:
    """Waits for a **stable** state, via the `status` query.

    The `after=` parameter used to exist here and was **never read**: `test_spec_refusal` believed
    it checked a pass through `refining` and checked nothing. It is not replaced: rebuilding
    a history by polling would be precisely the same illusion.
    """
    await _wait_until(handle, f"state `{state}`", lambda s: s["state"] == state, timeout=timeout)


async def test_a_query_past_its_deadline_is_asked_again() -> None:
    """A `status` query that outlasts its deadline is retried, not raised (#492, #496)."""
    from temporalio.service import RPCError, RPCStatusCode

    class SlowThenAnswering:
        def __init__(self) -> None:
            self.calls = 0

        async def query(self, name: str, **kwargs: Any) -> Any:
            self.calls += 1
            if self.calls == 1:
                raise RPCError("query deadline exceeded", RPCStatusCode.DEADLINE_EXCEEDED, b"")
            if self.calls == 2:
                raise RPCError("Timeout expired", RPCStatusCode.CANCELLED, b"")
            return {"state": "awaiting_spec_approval"}

    handle = SlowThenAnswering()
    await _wait_state(handle, "awaiting_spec_approval", timeout=5)
    assert handle.calls == 3


async def test_any_other_query_error_still_fails() -> None:
    """Only DEADLINE_EXCEEDED is retried: a workflow that is gone still fails the test."""
    from temporalio.service import RPCError, RPCStatusCode

    class Gone:
        async def query(self, name: str, **kwargs: Any) -> Any:
            raise RPCError("workflow not found", RPCStatusCode.NOT_FOUND, b"")

    with pytest.raises(RPCError):
        await _wait_state(Gone(), "awaiting_spec_approval", timeout=5)


# ──────────────────────────────── a dead ticket shows (2026-09-24) ────────────────────────────────


async def _ticket(setup: Fixture) -> Any:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        session.expunge(item)
        return item


async def test_an_interpreter_that_dies_says_so(setup: Fixture, temporal_env: Any) -> None:
    """A final activity failure kills the workflow, and the ticket carries the cause.

    Bench of 2026-09-24: two dead HR tickets, one on an activity, the other on a
    heartbeat, and nothing in the database said so. The workflow now writes why it dies.
    """
    from temporalio import activity
    from temporalio.client import WorkflowFailureError
    from temporalio.exceptions import ApplicationError

    @activity.defn(name="await_run")
    async def await_run_that_dies(payload: dict[str, Any]) -> dict[str, Any]:
        raise ApplicationError("the project no longer has a repository", non_retryable=True)

    async with worker_with(temporal_env, {"await_run": await_run_that_dies}):
        handle = await start(temporal_env, setup)
        with pytest.raises(WorkflowFailureError):
            await handle.result()

    item = await _ticket(setup)
    assert item.failure is not None, "the ticket does not say its interpreter died"
    assert item.failure["message"] == "the project no longer has a repository"
    assert item.failure["activity"] == "await_run"
    assert item.failure["state"] == "inbox"  # the state the step was leaving from

    from choregos_api.db.models import Event
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        types = (
            (await session.execute(select(Event.type).where(Event.work_item_id == setup.work_item_id)))
            .scalars()
            .all()
        )
    assert "choregos.workitem.workflow_failed" in types


async def test_an_error_without_a_message_still_leaves_its_type(setup: Fixture, temporal_env: Any) -> None:
    """A network error is often raised WITHOUT a message: the ticket must at least name its class.

    Replays the incident of 2026-09-27 on the dev tenant. `start_run` raised an
    `httpx.ConnectTimeout`: the Kubernetes API was unreachable, the `executor` queue being the only one
    to which the Cilium policy opens the port. The ticket recorded `"Application error"`, the
    default string of Temporal: nothing on the cause, nothing on the network, and the real error
    lived only in the orchestrator's log.
    """
    import httpx
    from temporalio import activity
    from temporalio.client import WorkflowFailureError

    @activity.defn(name="start_run")
    async def start_run_unreachable(payload: dict[str, Any]) -> dict[str, Any]:
        raise httpx.ConnectTimeout("")  # as httpx raises it: no message

    async with worker_with(temporal_env, {"start_run": start_run_unreachable}):
        handle = await start(temporal_env, setup)
        with pytest.raises(WorkflowFailureError):
            await handle.result()

    item = await _ticket(setup)
    assert item.failure is not None
    assert item.failure["message"] == "ConnectTimeout", (
        f"the ticket must name the error class without a message (seen: {item.failure['message']!r})"
    )
    assert item.failure["activity"] == "start_run"


async def test_an_interpreter_that_restarts_clears_the_mark(setup: Fixture, temporal_env: Any) -> None:
    """The mark falls on restart: a restarted ticket is no longer "dead"."""
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from temporalio import activity
    from temporalio.exceptions import ApplicationError

    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.failure = {"message": "dead a first time", "at": "2026-09-24T15:04:00+00:00"}

    @activity.defn(name="await_run")
    async def await_run_that_blocks(payload: dict[str, Any]) -> dict[str, Any]:
        raise ApplicationError("we stop here", non_retryable=True)

    async with worker_with(temporal_env, {"await_run": await_run_that_blocks}):
        handle = await start(temporal_env, setup)
        with pytest.raises(Exception):  # noqa: B017 - the death of this workflow is intended
            await handle.result()
    # `load_context` cleared the old mark at the start; the new death set its own
    item = await _ticket(setup)
    assert item.failure is not None
    assert item.failure["message"] == "we stop here"


async def test_an_uncollected_diff_does_not_kill_the_step(setup: Fixture, temporal_env: Any) -> None:
    """`collect_run_artifacts` serves the display: its failure does not cost the ticket (RH-1, 24/09)."""
    from temporalio import activity
    from temporalio.exceptions import ApplicationError

    scripted(
        setup.adapters,
        stage_result("spec written", outputs={"size": "M", "risk": "low", "spec_markdown": "## Spec"}),
    )

    @activity.defn(name="collect_run_artifacts")
    async def collect_that_fails(payload: dict[str, Any]) -> dict[str, Any]:
        raise ApplicationError("the project has no repository", non_retryable=True)

    async with worker_with(temporal_env, {"collect_run_artifacts": collect_that_fails}):
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "awaiting_spec_approval")

    assert (await _ticket(setup)).failure is None


async def test_await_run_is_replayed_after_a_transient_failure(setup: Fixture, temporal_env: Any) -> None:
    """A worker that restarts during a run (missed heartbeat) no longer kills the ticket."""
    from choregos_orchestrator.activities import stage as stage_activities
    from temporalio import activity
    from temporalio.exceptions import ApplicationError

    scripted(
        setup.adapters,
        stage_result("spec written", outputs={"size": "M", "risk": "low", "spec_markdown": "## Spec"}),
    )
    calls: list[int] = []

    @activity.defn(name="await_run")
    async def await_run_fragile(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(1)
        if len(calls) == 1:
            raise ApplicationError("heartbeat missed: the worker restarted")  # replayable
        return await stage_activities.await_run(payload)

    async with worker_with(temporal_env, {"await_run": await_run_fragile}):
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "awaiting_spec_approval")

    assert len(calls) >= 2
    assert (await _ticket(setup)).failure is None


async def test_the_gate_verdicts_are_in_the_run_journal(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """Which gates ran, and what they answered: it is in the run journal."""
    from choregos_api.db.models import Run, RunEvent
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    scripted(
        setup.adapters,
        stage_result("spec written", outputs={"size": "M", "risk": "low", "spec_markdown": "## Spec"}),
        stage_result(
            "implemented", artifacts={"branch": "choregos/123", "commits": ["fix(orders): credit notes"]}
        ),
        stage_result("verified"),
    )
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "awaiting_spec_approval")
        await handle.signal(
            "human_decision", {"approved": True, "decided_by": "augustin", "kind": "approval"}
        )
        # the implementation carries the perimeter and evidence gates
        await _wait_state(handle, "pr_open")

    async with session_scope(orgs="*") as session:
        runs = (
            (await session.execute(select(Run).where(Run.work_item_id == setup.work_item_id))).scalars().all()
        )
        rows = (
            (
                await session.execute(
                    select(RunEvent)
                    .where(RunEvent.run_id.in_([r.id for r in runs]), RunEvent.type == "gate.outcome")
                    .order_by(RunEvent.seq)
                )
            )
            .scalars()
            .all()
        )
    assert rows, "no verdict logged"
    names = {r.payload["name"] for r in rows}
    assert "scope_respected" in names or "evidence_present" in names, names
    assert all(isinstance(r.payload["passed"], bool) and "detail" in r.payload for r in rows)


# ──────────────── a failing notification does not kill a ticket (2026-09-27) ────────────────


async def test_a_failing_notifier_does_not_kill_the_human_gate(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """The human request must exist even when nobody can be notified.

    On the dev tenant, without Slack configured, `notify.send` raised, and the exception cancelled the
    TRANSACTION, hence the request that had just been written. The five attempts failed at the same
    place and the ticket died on "[slack] neither `bot_token` nor `webhook_url` configured". The
    human gate was therefore unreachable on any installation without a notifier, and the message
    blamed Slack instead of saying "the request is there, nobody was notified".
    """
    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    setup.adapters.notify.failure = "[slack] neither `bot_token` nor `webhook_url` configured"
    scripted(setup.adapters, refine_result("spec written"))

    async def requests_of_the_item() -> list[Any]:
        async with session_scope() as session:
            return list(
                (
                    await session.execute(
                        select(HumanRequest).where(HumanRequest.work_item_id == setup.work_item_id)
                    )
                )
                .scalars()
                .all()
            )

    async with worker_factory():
        handle = await start(temporal_env, setup)
        # The human wait state is reached: the gate works without notification.
        await _wait_state(handle, "awaiting_spec_approval")

        # WAIT for the request, do not read it right away. The interpreter sets the state BEFORE
        # calling `create_human_request`: reading right after `_wait_state` won locally and
        # lost in CI: 0 requests, not because it had been cancelled but because it
        # was not written yet. A test that depends on the machine speed says nothing
        # about the code (same lesson as `test_pause_and_resume`, 2026-09-24).
        import asyncio

        for _ in range(300):
            if await requests_of_the_item():
                break
            await asyncio.sleep(0.1)

    assert len(await requests_of_the_item()) == 1, "the human request was cancelled with the notification"

    item = await _ticket(setup)
    assert item.failure is None, f"the ticket died of a notification: {item.failure}"

    # And the silence shows: the event says that nobody was notified.
    from choregos_api.db.models import Event

    async with session_scope() as session:
        events = (
            (
                await session.execute(
                    select(Event).where(
                        Event.work_item_id == setup.work_item_id,
                        Event.type == "choregos.workitem.human_requested",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert events, "no human request event"
    assert any((e.payload or {}).get("notified") is False for e in events), (
        "an unrecorded silence replaces a noisy failure with a mute one"
    )


async def test_the_reminder_without_a_notifier_keeps_the_interpreter_alive(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """#497: the 24 h reminder of a pending human request must not kill the interpreter.

    On the dev tenant (no Slack), `_remind` ran the `notify` activity with `NO_RETRY`; it raised
    the Slack "no `bot_token` nor `webhook_url`" error and the activity failure failed the whole
    WorkItem interpreter, leaving the work item in its human state with nobody to receive the
    decision (globex/choregos-sandbox-dev#4 and #6, 10/11 and 10/13)."""
    from datetime import timedelta

    from temporalio.client import WorkflowExecutionStatus

    setup.adapters.notify.failure = "[slack] neither `bot_token` nor `webhook_url` configured"
    scripted(setup.adapters, refine_result("spec written"))

    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "awaiting_spec_approval")
        # Past the 24 h SLA, short of the 72 h timeout: the reminder fires, the request still waits.
        await temporal_env.sleep(timedelta(hours=25))
        await _wait_state(handle, "awaiting_spec_approval")
        description = await handle.describe()
        history = await handle.fetch_history()

    reminders = [
        event
        for event in history.events
        if event.activity_task_scheduled_event_attributes.activity_type.name == "notify"
    ]
    assert reminders, "the reminder never fired: the test proves nothing"
    assert description.status == WorkflowExecutionStatus.RUNNING, (
        f"the interpreter died of a reminder: {description.status}"
    )
    item = await _ticket(setup)
    assert item.failure is None, f"the work item died of a reminder: {item.failure}"


async def _refine_attempts(setup: Fixture, attempts: int) -> None:
    """The project's workflow, with `t-refine` retried up to `attempts` times."""
    from choregos_api.db.models import WorkflowDef
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        row = (
            await session.execute(select(WorkflowDef).where(WorkflowDef.project_id == setup.project_id))
        ).scalar_one()
        document = dict(row.json_doc)
        document["transitions"] = [
            {**t, "on_fail": {**t["on_fail"], "max_attempts": attempts}} if t["id"] == "t-refine" else t
            for t in document["transitions"]
        ]
        row.json_doc = document


async def test_the_same_failure_twice_escalates_instead_of_retrying(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """S27-07: three attempts allowed, but the second fails exactly like the first — no third."""
    await _refine_attempts(setup, 3)
    failure = StageResult(
        status=StageStatus.FAILED, summary="pytest: 3 failed in 12.4s", reason="tests_failed"
    )
    same = StageResult(status=StageStatus.FAILED, summary="pytest: 3 failed in 9.1s", reason="tests_failed")
    scripted(setup.adapters, failure, same, failure)
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "needs_human")
        status = await handle.query("status")
        assert status["attempts"]["t-refine"] == 2, status["attempts"]
        await handle.signal("control", {"action": "stop"})
        await handle.result()
        history = await handle.fetch_history()
    assert await _runs(setup) == 2
    # The decision is behind `workflow.patched`: the history it writes replays, and so do the
    # archived ones written before it (`tests/replay`). Set CHOREGOS_RECORD_REPLAY=1 to archive it.
    import os
    from pathlib import Path

    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if os.environ.get("CHOREGOS_RECORD_REPLAY") == "1":
        archive = (
            Path(__file__).resolve().parents[3] / "tests/replay/histories/wi-identical-failure-S27-07.json"
        )
        archive.write_text(history.to_json(), encoding="utf-8")


async def test_different_failures_use_every_attempt(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    await _refine_attempts(setup, 3)
    scripted(
        setup.adapters,
        StageResult(status=StageStatus.FAILED, summary="pytest: 3 failed", reason="tests_failed"),
        StageResult(status=StageStatus.FAILED, summary="lint: 2 errors", reason="lint_failed"),
        StageResult(status=StageStatus.FAILED, summary="pytest: 3 failed", reason="tests_failed"),
    )
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "needs_human")
        assert (await handle.query("status"))["attempts"]["t-refine"] == 3
        await handle.signal("control", {"action": "stop"})
        await handle.result()
