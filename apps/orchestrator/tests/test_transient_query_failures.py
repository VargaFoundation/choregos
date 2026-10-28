"""`query_soon` asks a query again on a transient failure only, and not for long (#873).

A `status` query failed a CI run with `TMPRL1101` ("Potential deadlock detected: workflow didn't
yield within 2 second(s)"). Reproduced under load: the query's activation took 0.3 ms of CPU, but
the test process stalled for 3 to 5 s (a full garbage collection, 0.2 s of CPU on an idle machine,
stretched by the load), and the worker's two-second timer fired before the finished activation was
read. The polling loops of this suite query through `query_soon`; these tests pin down what it
retries, what it does not, and when it gives up.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from temporalio.client import WorkflowQueryFailedError
from temporalio.service import RPCError, RPCStatusCode

from .conftest import is_transient_query_failure, query_soon

DEADLOCK = "[TMPRL1101] Potential deadlock detected: workflow didn't yield within 2 second(s)."


class ScriptedHandle:
    """A workflow handle whose `query` raises the scripted errors in turn, then answers."""

    def __init__(self, *errors: BaseException, answer: Any = None) -> None:
        self.errors = list(errors)
        self.answer = answer
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def query(self, name: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((name, args, kwargs))
        if self.errors:
            raise self.errors.pop(0)
        return self.answer


async def test_transient_failures_are_asked_again_until_the_query_answers() -> None:
    handle = ScriptedHandle(
        WorkflowQueryFailedError(DEADLOCK),
        RPCError("query deadline exceeded", RPCStatusCode.DEADLINE_EXCEEDED, b""),
        RPCError("query deadline exceeded", RPCStatusCode.UNKNOWN, b""),
        answer={"state": "pr_open"},
    )

    status = await query_soon(handle, "status", pause=0.01, rpc_timeout=7)

    assert status == {"state": "pr_open"}
    assert handle.calls == [("status", (), {"rpc_timeout": 7})] * 4, "every call is the same query"


@pytest.mark.parametrize(
    "error",
    [
        WorkflowQueryFailedError("Query handler for 'statut' expected but not found, known queries: [...]"),
        WorkflowQueryFailedError("KeyError: 'state'"),
        RPCError("workflow not found for ID: wi-billing-api-123", RPCStatusCode.NOT_FOUND, b""),
        RPCError("Timeout expired", RPCStatusCode.CANCELLED, b""),
        AssertionError("not a query error at all"),
    ],
    ids=["unknown-query", "query-handler-raised", "workflow-gone", "own-rpc-timeout", "other"],
)
async def test_any_other_failure_is_raised_at_once(error: BaseException) -> None:
    handle = ScriptedHandle(error, answer={"state": "pr_open"})

    with pytest.raises(type(error)) as raised:
        await query_soon(handle, "status", pause=0.01)

    assert raised.value is error
    assert len(handle.calls) == 1, "a failure that says something about the workflow is not retried"


async def test_the_budget_is_bounded_and_the_last_error_is_raised() -> None:
    """A query that never stops failing: `query_soon` gives up once its budget is spent, and raises
    the LAST error. Each failing call takes 0.1 s (a real one takes the worker's 2 s), so a budget
    of 1 s with a pause of 0.05 s leaves room for at most eight calls, however loaded the machine."""

    class AlwaysDeadlocked:
        def __init__(self) -> None:
            self.raised: list[WorkflowQueryFailedError] = []

        async def query(self, name: str, *args: Any, **kwargs: Any) -> Any:
            await asyncio.sleep(0.1)
            self.raised.append(WorkflowQueryFailedError(f"{DEADLOCK} (call {len(self.raised) + 1})"))
            raise self.raised[-1]

    handle = AlwaysDeadlocked()

    with pytest.raises(WorkflowQueryFailedError) as raised:
        await asyncio.wait_for(query_soon(handle, "status", budget=1.0, pause=0.05), timeout=30)

    assert 2 <= len(handle.raised) <= 8, len(handle.raised)
    assert raised.value is handle.raised[-1], "the error raised is the last one, not the first"


def test_only_the_deadlock_detector_and_query_deadlines_are_transient() -> None:
    assert is_transient_query_failure(WorkflowQueryFailedError(DEADLOCK))
    assert is_transient_query_failure(RPCError("deadline", RPCStatusCode.DEADLINE_EXCEEDED, b""))
    assert is_transient_query_failure(RPCError("query deadline exceeded", RPCStatusCode.UNKNOWN, b""))
    assert not is_transient_query_failure(WorkflowQueryFailedError("Query handler for 'x' expected"))
    assert not is_transient_query_failure(RPCError("Timeout expired", RPCStatusCode.CANCELLED, b""))
    assert not is_transient_query_failure(RPCError("not found", RPCStatusCode.NOT_FOUND, b""))
    assert not is_transient_query_failure(TimeoutError(DEADLOCK))
