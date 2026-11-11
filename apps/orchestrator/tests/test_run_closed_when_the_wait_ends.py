"""When the orchestrator stops waiting for a run, its row is closed (S65-01, ADR 0143).

It used to stop waiting and say so nowhere a late process would read: `_abandon` asked the executor
to cancel, then set `failed`, all of it under one `suppress(Exception)` — a cancel that raised left
the run `running` — and wrote no result; `cancel_run` set `cancelled` and no result either. The
run's API only asked "is there a result?", so a pod that outlived its run could still post one.

Every way the wait ends now closes the row first — a terminal status, an end date and the result
the workflow is told, in one transaction, committed before the executor is asked to cancel — and
none of it depends on the cancel succeeding.
"""

from __future__ import annotations

from typing import Any

import pytest
import structlog
from choregos_contracts import ExecutorKind, StageResult, StageStatus
from choregos_core.domain import ExecRef, ExecStatus, StageJobSpec
from choregos_core.run_closure import run_is_closed

from .conftest import ON_POSTGRES, Fixture
from .test_gateway_key_through_the_secret import _run
from .test_result_from_journal import _journal, _started_run

pytestmark = pytest.mark.asyncio

DONE = StageResult(status=StageStatus.DONE, summary="fix applied")


class _Executor:
    """Answers the states it is given, one per poll, then repeats the last one; its `cancel` may
    fail, and reads the run's row as another connection sees it at that moment."""

    kind = ExecutorKind.K8S_JOB

    def __init__(self, *states: str, cancel_fails: bool = False, queue: bool = False) -> None:
        self.states = list(states)
        self.capabilities = frozenset({"queue"}) if queue else frozenset()
        self.cancel_fails = cancel_fails
        self.polls = 0
        #: What the row said each time the executor was asked to cancel: (status, reason of result).
        self.seen_at_cancel: list[tuple[str, str | None]] = []

    async def start(self, spec: StageJobSpec) -> ExecRef:
        return ExecRef(
            kind=self.kind, name=f"run-{spec.run_id}", namespace=spec.namespace, run_id=spec.run_id
        )

    async def status(self, ref: ExecRef) -> ExecStatus:
        state = self.states[min(self.polls, len(self.states) - 1)]
        self.polls += 1
        message = {"unknown": "Job not found", "pending": "waiting for a slot"}.get(state, "")
        return ExecStatus(state=state, message=message)  # type: ignore[arg-type]

    async def cancel(self, ref: ExecRef) -> None:
        row = await _run(str(ref.run_id))
        self.seen_at_cancel.append((row.status, (row.result or {}).get("reason")))
        if self.cancel_fails:
            raise RuntimeError("the cluster does not answer")

    async def logs(self, ref: ExecRef) -> Any:
        if False:
            yield ""


async def _started(setup: Fixture, executor: _Executor) -> str:
    setup.adapters.executor = executor
    return await _started_run(setup)


async def _await(setup: Fixture, run_id: str, **bounds: float) -> dict[str, Any]:
    from choregos_orchestrator.activities import stage as activities

    payload = {"project_id": setup.project_id, "run_id": run_id, "poll_seconds": 0.01}
    return await activities.await_run({**payload, "timeout_minutes": 0.0005, **bounds})


def _closed_as_told(row: Any, outcome: dict[str, Any], *, status: str = "failed") -> None:
    """The row is closed, and holds exactly what the workflow was told."""
    assert run_is_closed(row)
    assert row.status == status and row.ended_at is not None
    assert row.result == outcome["result"], "the row and the workflow tell one story"


# ───────────────────────── every way the wait ends ─────────────────────────


@pytest.mark.parametrize("cancel_fails", [False, True], ids=["cancelled", "cancel fails"])
async def test_a_run_over_its_budget_is_closed_whatever_the_cancel_does(
    setup: Fixture, cancel_fails: bool
) -> None:
    executor = _Executor("running", cancel_fails=cancel_fails)
    run_id = await _started(setup, executor)

    with structlog.testing.capture_logs() as logs:
        outcome = await _await(setup, run_id)

    assert outcome["status"] == "timed_out" and outcome["result"]["reason"] == "timeout"
    _closed_as_told(await _run(run_id), outcome)
    assert executor.seen_at_cancel == [("failed", "timeout")], (
        "closed and committed BEFORE the executor is asked to cancel"
    )
    failures = [entry for entry in logs if entry["event"] == "run.cancel_failed"]
    if cancel_fails:
        (failure,) = failures
        assert failure["run_id"] == run_id and failure["log_level"] == "warning"
        assert failure["project"] == setup.project_slug and "does not answer" in failure["error"]
    else:
        assert failures == []


async def test_a_run_never_admitted_is_closed_on_the_queue(setup: Fixture) -> None:
    executor = _Executor("pending", cancel_fails=True, queue=True)
    run_id = await _started(setup, executor)
    assert (await _run(run_id)).status == "queued"

    outcome = await _await(setup, run_id, timeout_minutes=10, queue_timeout_minutes=0.0005)

    assert outcome["status"] == "queue_timed_out" and outcome["result"]["reason"] == "queue_timeout"
    _closed_as_told(await _run(run_id), outcome)
    assert executor.seen_at_cancel == [("failed", "queue_timeout")]


async def test_a_run_the_executor_lost_is_closed(setup: Fixture) -> None:
    executor = _Executor("running", "unknown", cancel_fails=True)
    run_id = await _started(setup, executor)

    outcome = await _await(setup, run_id, timeout_minutes=1)

    assert outcome["status"] == "lost" and outcome["result"]["reason"] == "lost"
    _closed_as_told(await _run(run_id), outcome)
    assert executor.seen_at_cancel == [("failed", "lost")]


@pytest.mark.parametrize(
    ("state", "outcome_status", "reason"),
    [
        ("failed", "failed", "failed"),
        ("cancelled", "cancelled", "cancelled"),
        ("succeeded", "missing_result", "invalid_result"),
    ],
)
async def test_a_pod_that_ended_without_a_result_closes_its_run(
    setup: Fixture, state: str, outcome_status: str, reason: str
) -> None:
    """The executor says the pod is dead: nothing to cancel, and the wait ends here."""
    executor = _Executor("running", state)
    run_id = await _started(setup, executor)

    outcome = await _await(setup, run_id, timeout_minutes=1)

    assert outcome["status"] == outcome_status and outcome["result"]["reason"] == reason
    _closed_as_told(await _run(run_id), outcome)
    assert executor.seen_at_cancel == []


@pytest.mark.parametrize("state", ["succeeded", "unknown"], ids=["pod ended", "pod lost"])
async def test_a_result_read_from_the_journal_closes_the_run_with_its_own_status(
    setup: Fixture, state: str
) -> None:
    run_id = await _started(setup, _Executor(state))
    await _journal(run_id, {"stage_result": DONE.model_dump(mode="json", by_alias=True)})

    outcome = await _await(setup, run_id, timeout_minutes=1)

    assert outcome["status"] == "finished"
    _closed_as_told(await _run(run_id), outcome, status="succeeded")


@pytest.mark.parametrize("cancel_fails", [False, True], ids=["cancelled", "cancel fails"])
async def test_a_cancelled_run_is_closed_whatever_the_cancel_does(setup: Fixture, cancel_fails: bool) -> None:
    from choregos_orchestrator.activities.execution import cancel_run

    executor = _Executor("running", cancel_fails=cancel_fails)
    run_id = await _started(setup, executor)

    with structlog.testing.capture_logs() as logs:
        await cancel_run({"project_id": setup.project_id, "run_id": run_id})

    row = await _run(run_id)
    assert run_is_closed(row) and row.status == "cancelled" and row.ended_at is not None
    result = StageResult.model_validate(row.result)
    assert (result.status, result.reason) == (StageStatus.FAILED, "cancelled")
    assert executor.seen_at_cancel == [("cancelled", "cancelled")], "closed before the cancel, not after"
    assert [e["run_id"] for e in logs if e["event"] == "run.cancel_failed"] == (
        [run_id] if cancel_fails else []
    )


async def test_a_queued_run_whose_interpreter_dies_is_closed_and_its_running_run_is_not(
    setup: Fixture,
) -> None:
    """A dying interpreter removes its queued run (#501) and closes it. Its running run is not
    abandoned: a restarted interpreter awaits it again (ADR 0008), so it stays open."""
    from choregos_adapters.executor.k8s_job import KubernetesJobExecutor
    from choregos_adapters.fakes.kubernetes import FakeKubernetesApi
    from choregos_orchestrator.activities.interpretation import record_workflow_failure

    from .test_gateway_key_through_the_secret import _prepare_and_start

    # One slot: the second run waits for it, as a suspended Job.
    setup.adapters.executor = KubernetesJobExecutor(client=FakeKubernetesApi(), max_active=1)  # type: ignore[arg-type]
    running = await _prepare_and_start(setup, attempt=1)
    queued = await _prepare_and_start(setup, attempt=2)
    assert not run_is_closed(await _run(queued)), "a queued run is open"

    await record_workflow_failure(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "message": "await_run: schedule-to-close timeout",
            "activity": "await_run",
        }
    )

    row = await _run(queued)
    assert run_is_closed(row) and row.status == "failed" and row.ended_at is not None
    result = StageResult.model_validate(row.result)
    assert (result.status, result.reason) == (StageStatus.FAILED, "cancelled")
    assert "never admitted" in result.summary
    still = await _run(running)
    assert not run_is_closed(still) and still.status == "running" and still.result is None


# ───────────────────────── replayed, and never over the runner's result ─────────────────────────


async def test_awaiting_a_closed_run_again_reads_its_result_back(setup: Fixture) -> None:
    """ADR 0008: `await_run` retried after it gave up does not wait a second budget, and does not
    tell the workflow another story."""
    executor = _Executor("running")
    run_id = await _started(setup, executor)
    first = await _await(setup, run_id)
    closed = await _run(run_id)
    polls = executor.polls

    again = await _await(setup, run_id)

    assert again == {"status": "finished", "result": first["result"]}
    assert executor.polls == polls, "nothing was polled: the row answered"
    row = await _run(run_id)
    assert (row.status, row.result, row.ended_at) == (closed.status, closed.result, closed.ended_at)


async def test_cancelling_twice_changes_nothing(setup: Fixture) -> None:
    from choregos_orchestrator.activities.execution import cancel_run

    executor = _Executor("running", cancel_fails=True)
    run_id = await _started(setup, executor)
    await cancel_run({"project_id": setup.project_id, "run_id": run_id})
    closed = await _run(run_id)

    await cancel_run({"project_id": setup.project_id, "run_id": run_id})

    row = await _run(run_id)
    assert (row.status, row.result, row.ended_at) == (closed.status, closed.result, closed.ended_at)
    assert executor.seen_at_cancel == [("cancelled", "cancelled")] * 2, "the cleanup is tried again"


async def test_a_run_abandoned_then_cancelled_keeps_how_it_ended(setup: Fixture) -> None:
    from choregos_orchestrator.activities.execution import cancel_run

    run_id = await _started(setup, _Executor("running"))
    outcome = await _await(setup, run_id)
    closed = await _run(run_id)

    await cancel_run({"project_id": setup.project_id, "run_id": run_id})

    row = await _run(run_id)
    assert (row.status, row.result, row.ended_at) == ("failed", outcome["result"], closed.ended_at)


async def _post_as_the_runner(run_id: str, result: StageResult) -> dict[str, Any]:
    """What the run's API writes when the runner posts its result."""
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    payload = result.model_dump(mode="json", by_alias=True)
    async with session_scope() as session:
        run = await session.get(Run, run_id)
        assert run is not None
        run.result = payload
        run.status = "succeeded" if result.ok else "failed"
        run.ended_at = utcnow()
    return payload


async def test_a_result_the_runner_posted_first_is_never_overwritten(setup: Fixture) -> None:
    """The budget ends and the result lands at the same moment: the result wins, and the workflow
    is told the result — never a timeout the row does not hold."""
    from choregos_orchestrator.activities import execution

    executor = _Executor("running", cancel_fails=True)
    run_id = await _started(setup, executor)
    posted = await _post_as_the_runner(run_id, DONE)
    before = await _run(run_id)
    ref = ExecRef(kind=executor.kind, name=f"run-{run_id}", namespace="runners", run_id=run_id)

    told = await execution._abandon(
        setup.project_id, run_id, ref, "timed_out", StageResult.failure("over 30 min", "timeout")
    )
    await execution.cancel_run({"project_id": setup.project_id, "run_id": run_id})

    assert told == {"status": "finished", "result": posted}
    row = await _run(run_id)
    assert (row.status, row.result, row.ended_at) == ("succeeded", posted, before.ended_at)


# ───────────────────────── what is not closed ─────────────────────────


async def _asleep_on_a_question(setup: Fixture) -> tuple[dict[str, Any], dict[str, Any]]:
    """A run whose agent asked a person, its sandbox suspended (ADR 0016): its ids, what it asked."""
    from choregos_orchestrator.activities.suspension import suspend_run

    executor = setup.adapters.executor
    executor.capabilities = frozenset({"suspend", "resume"})
    run_id = await _started_run(setup)
    ids = {"project_id": setup.project_id, "run_id": run_id}
    question = StageResult(status=StageStatus.NEEDS_HUMAN, summary="which currency?", reason="question")
    asked = await _post_as_the_runner(run_id, question)
    assert (await suspend_run(ids))["suspended"] is True
    return ids, asked


async def test_a_suspended_run_is_closed_while_it_sleeps_and_open_once_resumed(setup: Fixture) -> None:
    """A run has no `suspended` status: it waits for a person holding the result that asked the
    question. `suspend_run` leaves the row alone, and `resume_run` is what opens it again."""
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.suspension import resume_run

    ids, asked = await _asleep_on_a_question(setup)
    asleep = await _run(ids["run_id"])
    assert run_is_closed(asleep) and asleep.result == asked, "closed while its sandbox sleeps"

    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.documents = {**(item.documents or {}), "human_answer": "the order currency"}
    assert (await resume_run({**ids, "answer_id": "answer-1"}))["resumed"] is True

    resumed = await _run(ids["run_id"])
    assert not run_is_closed(resumed), "the same run goes on: it is open again"
    assert (resumed.status, resumed.result, resumed.ended_at) == ("running", None, None)


async def test_cancelling_a_suspended_run_keeps_the_question_it_asked(setup: Fixture) -> None:
    from choregos_orchestrator.activities.execution import cancel_run

    ids, asked = await _asleep_on_a_question(setup)
    before = await _run(ids["run_id"])

    await cancel_run(ids)

    row = await _run(ids["run_id"])
    assert (row.status, row.result, row.ended_at) == (before.status, asked, before.ended_at)


# ───────────────────────── at the same moment (PostgreSQL) ─────────────────────────


async def _waiting_for_a_lock() -> int:
    """The application role's sessions that wait for a lock, seen as a superuser."""
    import asyncpg

    from .conftest import PG_URL
    from .pg_kit import APP_ROLE

    connection = await asyncpg.connect(PG_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        return int(
            await connection.fetchval(
                "SELECT count(*) FROM pg_stat_activity WHERE usename = $1 AND wait_event_type = 'Lock'"
                " AND datname = current_database()",
                APP_ROLE,
            )
        )
    finally:
        await connection.close()


@pytest.mark.skipif(not ON_POSTGRES, reason="CHOREGOS_TEST_ORCH_ON_POSTGRES is not set")
async def test_a_result_being_posted_is_waited_for_not_overwritten(setup: Fixture) -> None:
    """The runner's result is written and not committed yet when the wait ends. The close reads the
    row under the lock an update takes: it waits for the post, then leaves the result alone.
    SQLite has one writer and no row lock: only a real PostgreSQL shows this."""
    import asyncio

    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow
    from choregos_orchestrator.activities.execution import cancel_run

    run_id = await _started(setup, _Executor("running"))
    posted = DONE.model_dump(mode="json", by_alias=True)
    loop = asyncio.get_running_loop()

    async with session_scope() as posting:
        run = await posting.get(Run, run_id)
        assert run is not None
        run.result, run.status, run.ended_at = posted, "succeeded", utcnow()
        await posting.flush()

        closing = asyncio.create_task(cancel_run({"project_id": setup.project_id, "run_id": run_id}))
        deadline = loop.time() + 20
        while not await _waiting_for_a_lock():
            assert not closing.done(), "the close did not wait for the result being posted"
            assert loop.time() < deadline, "the close never reached the row"
            await asyncio.sleep(0.02)
    await asyncio.wait_for(closing, timeout=20)

    row = await _run(run_id)
    assert (row.status, row.result) == ("succeeded", posted), "the runner's result stands"
