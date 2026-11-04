"""The real Temporal client against a simulated client, and the in-memory event bus.

`RealTemporal.describe()` is what makes a dead work item visible (P0-2); it was only
exercised by the fake client. Here a Temporal client is simulated object by object, the
shape `temporalio` returns, to prove the translation of the state, the readable cause of a
failure, and the "unknown" fallback when Temporal does not answer.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from choregos_api.events import EventBus, _payload_of, emit
from choregos_api.temporal import RealTemporal, _readable_cause
from choregos_contracts import EventType


class _Handle:
    def __init__(self, status_name: str | None, error: BaseException | None = None) -> None:
        self.status_name = status_name
        self.error = error
        self.signals: list[tuple[str, Any]] = []
        self.cancelled = False

    async def describe(self) -> Any:
        status = SimpleNamespace(name=self.status_name) if self.status_name else None
        return SimpleNamespace(status=status)

    async def result(self) -> Any:
        if self.error:
            raise self.error
        return {}

    async def signal(self, name: str, payload: Any) -> None:
        self.signals.append((name, payload))

    async def query(self, name: str) -> Any:
        return {"query": name}

    async def cancel(self) -> None:
        self.cancelled = True


class _Client:
    def __init__(self, handle: _Handle) -> None:
        self.handle = handle

    def get_workflow_handle(self, workflow_id: str) -> _Handle:
        return self.handle


def _temporal(handle: _Handle) -> RealTemporal:
    temporal = RealTemporal("temporal:7233", "default", "orchestrator")
    temporal._client = _Client(handle)
    return temporal


async def test_a_running_workflow_is_described_without_a_cause() -> None:
    described = await _temporal(_Handle("RUNNING")).describe("wi-1")
    assert described is not None and described.status == "RUNNING" and described.failure is None
    without_status = await _temporal(_Handle(None)).describe("wi-1")
    assert without_status is not None and without_status.status == "RUNNING"


async def test_a_failed_workflow_carries_the_deepest_cause() -> None:
    """ "the project has no repository", not "Activity task failed"."""
    deepest = ValueError("the project has no repository")
    middle = RuntimeError("Activity task failed")
    middle.__cause__ = deepest
    top = RuntimeError("Workflow execution failed")
    top.__cause__ = middle
    described = await _temporal(_Handle("FAILED", top)).describe("wi-1")
    assert (
        described is not None
        and described.status == "FAILED"
        and described.failure == "the project has no repository"
    )
    assert _readable_cause(SimpleNamespace(message="m", __cause__=None)) == "m"  # type: ignore[arg-type]


async def test_a_missing_or_slow_temporal_answers_unknown_rather_than_failing() -> None:
    class _Slow(_Handle):
        async def describe(self) -> Any:
            await asyncio.sleep(10)

    temporal = _temporal(_Slow("RUNNING"))
    with pytest.MonkeyPatch.context() as mp:
        # two seconds of wait at most: we do not pay for them in a test
        import choregos_api.temporal as module

        original = module.asyncio.wait_for

        async def hurried(coro: Any, timeout: float) -> Any:
            return await original(coro, 0.01)

        mp.setattr(module.asyncio, "wait_for", hurried)
        assert await temporal.describe("wi-1") is None

    class _Broken(_Client):
        def get_workflow_handle(self, workflow_id: str) -> _Handle:
            raise OSError("connection refused")

    temporal._client = _Broken(_Handle("RUNNING"))
    assert await temporal.describe("wi-1") is None


async def test_signal_query_and_cancellation_go_through_the_handle() -> None:
    handle = _Handle("RUNNING")
    temporal = _temporal(handle)
    await temporal.signal("wi-1", "freeze", {"reason": "x"})
    assert handle.signals == [("freeze", {"reason": "x"})]
    assert await temporal.query("wi-1", "status_query") == {"query": "status_query"}
    await temporal.cancel("wi-1")
    assert handle.cancelled


async def test_the_bus_routes_by_project_subject_and_run_and_replays_after_an_identifier() -> None:
    bus = EventBus(buffer_size=3)
    first = bus_event(bus, EventType.RUN_STARTED, subject="r-1", project_slug="billing", run_id="r-1")
    second_event = bus_event(bus, EventType.RUN_PROGRESS, subject="r-1", project_slug="billing", run_id="r-1")
    bus_event(bus, EventType.WORKITEM_CREATED, subject="B-1", project_slug="other")
    assert [e.id for e in bus.replay("run:r-1")] == [first.id, second_event.id]
    assert [e.id for e in bus.replay("project:billing", after_id=first.id)] == [second_event.id]
    assert len(bus.replay("*")) == 3 and bus.replay("subject:B-1")[0].project_slug == "other"
    assert bus.replay("run:r-1", after_id="unknown") == bus.replay("run:r-1"), (
        "unknown identifier: everything"
    )
    for _ in range(5):
        bus_event(bus, EventType.RUN_PROGRESS, subject="r-1", project_slug="billing", run_id="r-1")
    assert len(bus.replay("run:r-1")) == 3, "the buffer is bounded"


async def test_a_subscriber_receives_the_replay_then_the_live_events() -> None:
    bus = EventBus()
    before = bus_event(bus, EventType.RUN_STARTED, subject="r-1", run_id="r-1")
    received: list[str] = []

    async def listen() -> None:
        stream = bus.subscribe("run:r-1")
        async for event in stream:
            received.append(str(event.type))
            if len(received) == 2:
                break
        # An abandoned async generator runs its `finally` only when it is closed:
        # this is what the SSE server does when the client leaves.
        await stream.aclose()

    task = asyncio.create_task(listen())
    await asyncio.sleep(0.01)
    assert bus.subscriber_count("run:r-1") == 1
    bus_event(bus, EventType.RUN_PROGRESS, subject="r-1", run_id="r-1")
    await asyncio.wait_for(task, 2)
    assert received == [str(before.type), str(EventType.RUN_PROGRESS)]
    assert bus.subscriber_count("run:r-1") == 0, "unsubscribed on exit"


def test_a_payload_too_heavy_is_lightened_for_the_notification() -> None:
    from choregos_api import events as module

    event = emit(
        EventType.RUN_PROGRESS,
        subject="r-1",
        run_id="r-1",
        state="running",
        text="x" * (module.MAX_PAYLOAD_BYTES + 100),
    )
    lightened = _payload_of(event)
    assert len(lightened.encode()) <= module.MAX_PAYLOAD_BYTES
    assert (
        '"truncated":true' in lightened and '"run_id":"r-1"' in lightened and '"state":"running"' in lightened
    )
    short = emit(EventType.RUN_PROGRESS, subject="r-1", run_id="r-1")
    assert '"truncated"' not in _payload_of(short)


def bus_event(bus: EventBus, type_: EventType, **data: Any) -> Any:
    from choregos_api.events import ChoregosEvent

    subject = data.pop("subject", None)
    project_slug = data.pop("project_slug", None)
    event = ChoregosEvent.emit(type_, source="/test", subject=subject, project_slug=project_slug, **data)
    bus.publish(event)
    return event
