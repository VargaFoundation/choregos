"""The A2A door across replicas and over time (ADR 0078 §4-5, S40-23f).

On PostgreSQL (the CI's API suite on PostgreSQL): a stream wakes up from `LISTEN` when another
replica persists an event, and two replicas delivering at once send a notification once. On every
backend, with the clocks moved instead of waited: the push retry schedule and its give-up, a host
closed after registration, the stream's hour and its fallback read, and a client that hangs up.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from .conftest import SUITE_SUR_POSTGRES
from .test_a2a_inbound import ENDPOINT, _ready, _send, _stream, _when_followed
from .test_a2a_push import Receiver, _config, _happen, _set, enforce, receiver

__all__ = ["enforce", "receiver"]  # fixtures, used by name

on_postgres = pytest.mark.skipif(
    not SUITE_SUR_POSTGRES,
    reason="NOTIFY and SKIP LOCKED: PostgreSQL only (CHOREGOS_TEST_SUITE_SUR_POSTGRES)",
)


def _naive(moment: datetime) -> datetime:
    return moment.replace(tzinfo=None)


# ───────────────────────────── across replicas (PostgreSQL) ─────────────────────────────


async def _persisted_elsewhere(task_id: str) -> None:
    """The work item finishes and its event is persisted, as another replica or the orchestrator
    would: through `persist_event`, which only `NOTIFY`s on PostgreSQL, at the commit."""
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services.evenements import persist_event
    from choregos_contracts import EventType

    async with session_scope(orgs="*") as session:
        item = await session.get(WorkItem, task_id)
        assert item is not None
        item.state = "deployed_prod"
        await persist_event(
            session,
            EventType.WORKITEM_STATE_CHANGED,
            project_id=item.project_id,
            work_item_id=item.id,
            subject=item.tracker_key,
            project_slug="billing-api",
        )


@on_postgres
async def test_a_stream_wakes_up_on_an_event_another_replica_persists(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.a2a import door
    from choregos_api.config import get_settings
    from choregos_api.events import RelaisPostgres

    monkeypatch.setattr(door, "STREAM_POLL", 60.0)  # within the test, only the event can wake it
    token = await _ready(client, project)
    task = await _send(client, token)
    relay = RelaisPostgres(get_settings().database_url)  # this replica's LISTEN, as the lifespan starts it
    relay.demarrer()
    try:
        await asyncio.sleep(0.5)  # LISTEN is in place
        frames, _ = await asyncio.gather(
            _stream(client, token, "tasks/resubscribe", {"id": task["id"]}),
            _when_followed(lambda: _persisted_elsewhere(task["id"])),
        )
    finally:
        await relay.arreter()
    results = [f["result"] for f in frames]
    assert [r["kind"] for r in results] == ["task", "status-update"]
    assert results[1]["status"]["state"] == "completed" and results[1]["final"] is True
    assert relay.recus >= 1, "the event came through NOTIFY, not this process's bus"


@on_postgres
async def test_two_replicas_delivering_at_once_send_a_notification_once(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.a2a.push import deliver_pending
    from choregos_api.services.remote_agents import set_a2a_transport

    calls: list[httpx.Request] = []

    async def slow(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        await asyncio.sleep(0.5)  # the first replica holds the row while the second looks
        return httpx.Response(200)

    set_a2a_transport(httpx.MockTransport(slow))
    try:
        token = await _ready(client, project)
        task = await _send(client, token)
        await _set(client, token, task["id"])
        await _happen(task["id"])
        first, second = await asyncio.gather(deliver_pending(), deliver_pending())
    finally:
        set_a2a_transport(None)
    assert len(calls) == 1, "the row lock keeps the second replica from sending it again"
    assert sorted([len(first), len(second)]) == [0, 1]


# ───────────────────────────── push notifications over time ─────────────────────────────


async def test_a_failed_notification_is_retried_on_a_growing_delay_then_given_up(
    client: AsyncClient, project: dict[str, Any], receiver: Receiver, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.a2a import push
    from choregos_core import utcnow

    now = [utcnow()]
    monkeypatch.setattr(push, "utcnow", lambda: now[0])
    token = await _ready(client, project)
    task = await _send(client, token)
    await _set(client, token, task["id"])
    await _happen(task["id"])
    receiver.status = 503
    delays: list[float] = []
    for attempt in range(1, push.MAX_ATTEMPTS + 1):
        assert await push.deliver_pending() == []
        config = await _config(task["id"])
        assert config.attempts == attempt and len(receiver.calls) == attempt
        if attempt == push.MAX_ATTEMPTS:
            break
        delay = (_naive(config.next_attempt_at) - _naive(now[0])).total_seconds()
        delays.append(delay)
        now[0] += timedelta(seconds=delay - 1)
        assert await push.deliver_pending() == []
        assert len(receiver.calls) == attempt, "not called again before its delay"
        now[0] += timedelta(seconds=1)
    assert delays == [20, 40, 80, 160, 320, 640, 1280]
    config = await _config(task["id"])
    assert config.finished_at is not None and "given up after 8 failed calls" in config.last_error
    now[0] += timedelta(hours=2)
    receiver.status = 200
    await _happen(task["id"])
    assert await push.deliver_pending() == []
    assert len(receiver.calls) == push.MAX_ATTEMPTS, "a registration given up stays silent"


async def test_a_host_closed_after_registration_is_refused_at_delivery(
    client: AsyncClient, project: dict[str, Any], receiver: Receiver, enforce: None
) -> None:
    from choregos_api.a2a.push import deliver_pending
    from choregos_api.db.models import Organization, OrgEgressRule
    from choregos_api.db.session import session_scope
    from sqlalchemy import select, update

    token = await _ready(client, project)
    task = await _send(client, token)
    async with session_scope(orgs="*") as session:
        org = (await session.execute(select(Organization).where(Organization.slug == "varga"))).scalar_one()
        session.add(OrgEgressRule(org_id=org.id, host="hooks.partner.example", status="allowed"))
    assert "result" in await _set(client, token, task["id"])
    async with session_scope(orgs="*") as session:
        await session.execute(update(OrgEgressRule).values(status="revoked"))
    await _happen(task["id"])
    assert await deliver_pending() == []
    assert receiver.calls == [], "the call never left the platform"
    config = await _config(task["id"])
    assert config.attempts == 1 and "Egress" in config.last_error and config.cursor_event_id is None


# ───────────────────────────── a stream over time ─────────────────────────────


def _recorded_wake(monkeypatch: pytest.MonkeyPatch, step: float = 0.0) -> list[float]:
    """Records how long each wait would be, waits a few milliseconds instead, and moves the
    stream's clock by `step` seconds each time."""
    from choregos_api.a2a import door

    waits: list[float] = []
    now = [10_000.0]
    original = door._wake

    async def wake(queue: asyncio.Queue[Any], tracker_key: str, wait: float) -> None:
        waits.append(wait)
        if len(waits) > 20:
            raise AssertionError(f"the stream outlived its hour: {waits[:5]}…")
        await original(queue, tracker_key, 0.05)
        now[0] += step

    monkeypatch.setattr(door, "_clock", lambda: now[0])
    monkeypatch.setattr(door, "_wake", wake)
    return waits


async def test_a_stream_ends_after_its_hour(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.a2a import door

    waits = _recorded_wake(monkeypatch, step=1795.0)
    token = await _ready(client, project)
    task = await _send(client, token)
    frames = await _stream(client, token, "tasks/resubscribe", {"id": task["id"]})
    assert [f["result"]["kind"] for f in frames] == ["task"], "closed, not final: the caller resubscribes"
    assert waits == [30.0, 30.0, 10.0], "a read every 30 s, the last one at the hour's end"
    assert door._STREAMS == {}


async def test_a_stream_reads_the_task_again_when_no_event_comes(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    waits = _recorded_wake(monkeypatch)
    token = await _ready(client, project)
    task = await _send(client, token)

    async def finish_silently() -> None:
        async with session_scope() as session:
            item = await session.get(WorkItem, task["id"])
            assert item is not None
            item.state = "deployed_prod"  # no event: only the fallback read can see it

    frames, _ = await asyncio.gather(
        _stream(client, token, "tasks/resubscribe", {"id": task["id"]}), _when_followed(finish_silently)
    )
    assert frames[-1]["result"]["status"]["state"] == "completed" and frames[-1]["result"]["final"] is True
    assert waits[0] == 30.0


async def test_a_client_that_hangs_up_frees_its_stream(
    app: Any, client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.a2a import door

    token = await _ready(client, project)
    task = await _send(client, token)
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 3, "method": "tasks/resubscribe", "params": {"id": task["id"]}}
    ).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": ENDPOINT,
        "raw_path": ENDPOINT.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"test"),
            (b"content-type", b"application/json"),
            (b"accept", b"text/event-stream"),
            (b"authorization", f"Bearer {token}".encode()),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("test", 80),
    }
    framed = asyncio.Event()
    asked: list[bool] = []
    while_open: list[dict[str, int]] = []

    async def receive() -> dict[str, Any]:
        if not asked:
            asked.append(True)
            return {"type": "http.request", "body": body, "more_body": False}
        await framed.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body" and b"data:" in message.get("body", b""):
            while_open.append(dict(door._STREAMS))
            framed.set()

    await asyncio.wait_for(app(scope, receive, send), 10)
    assert while_open and sum(while_open[0].values()) == 1, "the open stream held a slot"
    assert door._STREAMS == {}, "the slot is given back when the caller hangs up"
