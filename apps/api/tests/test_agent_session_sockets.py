"""A person on a hosted agent's own socket (S41-21, ADR 0080 §10): `GET …/agents/{slug}/realtime`.

The caller's frames are journaled and read by the pod (`…/socket/next`); the container's come back
numbered (`…/socket/out`) and reach the caller once each, in order. The pod is simulated here: a
task speaking the internal API with the session's token, as `choregos-runner serve-session` does,
echoing each frame it reads.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from httpx import AsyncClient

from .egress_support import as_person
from .test_agent_hosted_sessions import (  # noqa: F401
    ORG,
    _hosted,
    _token,
    pod_http,
    quick,
    registry,
    temporal,
)
from .test_agent_realtime import _events, _socket
from .test_agent_sessions import Upstream, upstream  # noqa: F401
from .ws_support import AsgiWebSocket, Closed

SOCKET = f"{ORG}/agents/triager/realtime?project=billing-api"


def _resume(session_id: str, after: int) -> str:
    return f"{SOCKET}&session={session_id}&after={after}"


@pytest.fixture
def grace(monkeypatch: pytest.MonkeyPatch) -> Callable[[float], None]:
    from choregos_api.services import agent_realtime

    def set_grace(seconds: float) -> None:
        monkeypatch.setattr(agent_realtime, "RESUME_GRACE_S", seconds)

    return set_grace


@dataclass
class Pod:
    """The session's pod: reads the caller's frames and answers each `in` with an echo; `close`
    closes the container's socket with that code once it has answered `close_after` frames."""

    http: AsyncClient
    session_id: str
    close: tuple[int, str] | None = None
    close_after: int = 0
    heard: list[dict[str, Any]] = field(default_factory=list)
    opened: int = 0

    def path(self, tail: str) -> str:
        return f"/api/v1/internal/agent-sessions/{self.session_id}/socket/{tail}"

    async def post(self, body: dict[str, Any]) -> Any:
        return await self.http.post(self.path("out"), json=body, headers=_token(self.session_id))

    async def run(self) -> None:
        cursor, out_n = 0, None
        while True:
            got = await self.http.get(
                self.path("next"), params={"after": cursor, "wait": 0.05}, headers=_token(self.session_id)
            )
            if got.status_code == 410:
                return
            assert got.status_code == 200, got.text
            body = got.json()
            out_n = body["out_n"] if out_n is None else out_n
            cursor = body["cursor"]
            for frame in body["frames"]:
                self.heard.append(frame)
                if frame["kind"] == "open":
                    self.opened += 1
                elif frame["kind"] == "close":
                    return
                elif frame["kind"] == "in":
                    out_n += 1
                    text = json.dumps({"type": "echo", "text": frame["text"]})
                    closed = None
                    if self.close is not None and out_n >= self.close_after:
                        closed = {"code": self.close[0], "reason": self.close[1]}
                    posted = await self.post({"frames": [{"n": out_n, "text": text}], "closed": closed})
                    assert posted.status_code == 200, posted.text
                    if closed is not None:
                        return


async def _session_id(socket: AsgiWebSocket) -> str:
    first = await socket.receive_json()
    assert first["type"] == "choregos.session", first
    return str(first["session_id"])


async def _opened(client: AsyncClient, http: AsyncClient, **pod: Any) -> tuple[AsgiWebSocket, Pod]:
    await _hosted(client, protocols=["invocations_ws"])
    socket = _socket(client, SOCKET)
    await socket.__aenter__()
    return socket, Pod(http, await _session_id(socket), **pod)


async def _state(client: AsyncClient, session_id: str) -> dict[str, Any]:
    read = await client.get(f"{ORG}/agent-sessions/{session_id}")
    assert read.status_code == 200, read.text
    found: dict[str, Any] = read.json()
    return found


async def _until(check: Callable[[], Any], timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not await check():
        assert asyncio.get_running_loop().time() < deadline, "the condition never held"
        await asyncio.sleep(0.02)


async def _text(socket: AsgiWebSocket) -> str:
    message = await socket._next()
    assert message["type"] == "websocket.send", message
    return str(message["text"])


def _echo(text: str) -> dict[str, Any]:
    return {"type": "echo", "text": text}


# ───────────────────────── a conversation ─────────────────────────


async def test_the_callers_frames_reach_the_pod_and_its_frames_come_back_once_each(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
    registry: Any,  # noqa: F811
    temporal: Any,  # noqa: F811
    pod_http: AsyncClient,  # noqa: F811
    quick: None,  # noqa: F811
) -> None:
    socket, pod = await _opened(client, pod_http)
    running = asyncio.create_task(pod.run())
    await socket.send_text("hi")
    assert await socket.receive_json() == _echo("hi")
    await socket.send_text("there")
    assert await socket.receive_json() == _echo("there")
    await socket.hang_up()
    await asyncio.wait_for(running, 5)

    assert temporal.start_calls == [f"agent-session-{pod.session_id}"], "one pod for the session"
    # The session closes as the caller's close is journaled: the pod reads it, or is told 410 first.
    assert [frame["kind"] for frame in pod.heard][:3] == ["open", "in", "in"]
    assert [frame["text"] for frame in pod.heard if frame["kind"] == "in"] == ["hi", "there"]
    read = await _state(client, pod.session_id)
    assert read["status"] == "closed" and read["reason"] == "closed"
    gone = await pod_http.get(pod.path("next"), headers=_token(pod.session_id))
    assert gone.status_code == 410, "a closed session's pod is told to stop"


async def test_a_frame_posted_again_is_recorded_once_and_a_hole_is_refused(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
    registry: Any,  # noqa: F811
    temporal: Any,  # noqa: F811
    pod_http: AsyncClient,  # noqa: F811
    quick: None,  # noqa: F811
) -> None:
    socket, pod = await _opened(client, pod_http)
    first = {"frames": [{"n": 1, "text": "one"}]}
    assert (await pod.post(first)).json() == {"out_n": 1}
    assert (await pod.post(first)).json() == {"out_n": 1}, "the same number again changes nothing"
    hole = await pod.post({"frames": [{"n": 3, "text": "three"}]})
    assert hole.status_code == 409, hole.text
    assert (await pod.post({"frames": [{"n": 2, "text": "two"}]})).json() == {"out_n": 2}

    assert [await _text(socket), await _text(socket)] == ["one", "two"], "as the container sent them"
    await socket.hang_up()
    journal = [event for event in await _events(pod.session_id) if event["type"] == "socket.out"]
    assert [event["payload"] for event in journal] == [{"n": 1, "text": "one"}, {"n": 2, "text": "two"}]


async def test_another_sessions_token_reads_nothing(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
    registry: Any,  # noqa: F811
    temporal: Any,  # noqa: F811
    pod_http: AsyncClient,  # noqa: F811
    quick: None,  # noqa: F811
) -> None:
    socket, pod = await _opened(client, pod_http)
    for method, tail in (("GET", "next"), ("POST", "out")):
        refused = await pod_http.request(
            method, pod.path(tail), json={"frames": []}, headers=_token("another-session")
        )
        assert refused.status_code == 403, (tail, refused.text)
    await socket.hang_up()


async def test_a_frame_journaled_while_the_pod_reads_is_sent_on_its_next_read(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
    registry: Any,  # noqa: F811
    temporal: Any,  # noqa: F811
    pod_http: AsyncClient,  # noqa: F811
    quick: None,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On PostgreSQL each statement reads its own snapshot: a frame committed between reading the
    frames and reading the cursor would be passed by the cursor, and never reach the container."""
    from choregos_api.db.models import AgentSessionRecord
    from choregos_api.services import agent_session_sockets as sockets
    from choregos_api.services.agent_sessions import journal

    socket, pod = await _opened(client, pod_http)
    cursor, kinds = 0, list[str]()
    while "open" not in kinds:  # the pod is asked for once, before the race is staged
        body = (await pod_http.get(pod.path("next"), headers=_token(pod.session_id))).json()
        kinds += [frame["kind"] for frame in body["frames"]]
    real, staged = sockets._events, False

    async def racing(db: Any, session_id: str, after: int, *types: str, **upto: Any) -> Any:
        nonlocal staged
        rows = await real(db, session_id, after, *types, **upto)
        if "socket.in" in types and not staged:
            staged = True  # the caller's frame lands just after the frames were read
            await journal(db, await db.get(AgentSessionRecord, session_id), "socket.in", {"text": "late"})
        return rows

    monkeypatch.setattr(sockets, "_events", racing)
    heard: list[str] = []
    for _ in range(2):
        got = await pod_http.get(pod.path("next"), params={"after": cursor}, headers=_token(pod.session_id))
        assert got.status_code == 200, got.text
        cursor = got.json()["cursor"]
        heard += [frame["text"] for frame in got.json()["frames"] if frame["kind"] == "in"]
    assert heard == ["late"], "the cursor never passes a frame the pod was not sent"
    await socket.hang_up()


# ───────────────────────── a socket that drops ─────────────────────────


async def test_a_caller_back_in_time_gets_what_it_missed_from_the_journal(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
    registry: Any,  # noqa: F811
    temporal: Any,  # noqa: F811
    pod_http: AsyncClient,  # noqa: F811
    quick: None,  # noqa: F811
) -> None:
    socket, pod = await _opened(client, pod_http)
    running = asyncio.create_task(pod.run())
    await socket.send_text("one")
    assert await socket.receive_json() == _echo("one")
    await socket.send_text("two")
    await socket.drop(wait=False)  # the container answers while the caller is away
    await _until(lambda: _out_count(pod.session_id, 2))

    async with _socket(client, _resume(pod.session_id, 1)) as back:
        assert await back.receive_json() == _echo("two"), "no `choregos.session` frame on a resume"
        await back.send_text("three")
        assert await back.receive_json() == _echo("three")
        await back.hang_up()
    await asyncio.wait_for(running, 5)

    assert temporal.start_calls == [f"agent-session-{pod.session_id}"], "the same pod, not a new one"
    assert pod.opened == 1, "the container's socket was opened once"
    journal = [event["type"] for event in await _events(pod.session_id)]
    assert journal.count("socket.attached") == 2 and "socket.detached" in journal
    assert (await _state(client, pod.session_id))["reason"] == "closed"


async def _out_count(session_id: str, count: int) -> bool:
    return sum(event["type"] == "socket.out" for event in await _events(session_id)) >= count


async def test_past_the_grace_the_session_closes_caller_gone(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
    registry: Any,  # noqa: F811
    temporal: Any,  # noqa: F811
    pod_http: AsyncClient,  # noqa: F811
    quick: None,  # noqa: F811
    grace: Callable[[float], None],
) -> None:
    grace(0.2)
    socket, pod = await _opened(client, pod_http)
    await socket.drop(wait=False)

    async def closed() -> bool:
        return (await _state(client, pod.session_id))["status"] != "open"

    await _until(closed)
    assert (await _state(client, pod.session_id))["reason"] == "caller_gone"
    assert (f"agent-session-{pod.session_id}", "close", {"reason": "caller_gone"}) in temporal.signals
    with pytest.raises(Closed) as late:
        async with _socket(client, _resume(pod.session_id, 0)):
            pass
    assert late.value.code == 1008 and "caller_gone" in late.value.reason


async def test_a_resume_past_what_the_container_sent_is_refused(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
    registry: Any,  # noqa: F811
    temporal: Any,  # noqa: F811
    pod_http: AsyncClient,  # noqa: F811
    quick: None,  # noqa: F811
) -> None:
    socket, pod = await _opened(client, pod_http)
    with pytest.raises(Closed) as refused:
        async with _socket(client, _resume(pod.session_id, 3)):
            pass
    assert refused.value.code == 1008 and "not 3" in refused.value.reason
    await socket.hang_up()


async def test_another_person_cannot_take_the_session(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
    registry: Any,  # noqa: F811
    temporal: Any,  # noqa: F811
    pod_http: AsyncClient,  # noqa: F811
    quick: None,  # noqa: F811
) -> None:
    socket, pod = await _opened(client, pod_http)
    async with as_person(client, "maintainer@varga.dev", "org_admin") as other:
        with pytest.raises(Closed) as refused:
            async with _socket(other, _resume(pod.session_id, 0)):
                pass
    assert refused.value.code == 1008 and "Agent session" in refused.value.reason
    await socket.hang_up()
    journal = [event["type"] for event in await _events(pod.session_id)]
    assert journal.count("socket.attached") == 1


async def test_a_newer_socket_takes_over_the_older_one(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
    registry: Any,  # noqa: F811
    temporal: Any,  # noqa: F811
    pod_http: AsyncClient,  # noqa: F811
    quick: None,  # noqa: F811
) -> None:
    old, pod = await _opened(client, pod_http)
    running = asyncio.create_task(pod.run())
    async with _socket(client, _resume(pod.session_id, 0)) as new:
        replaced = await old.closed()
        assert replaced.code == 1000 and "another socket" in replaced.reason
        await new.send_text("still there?")
        assert await new.receive_json() == _echo("still there?")
        await new.hang_up()
    await asyncio.wait_for(running, 5)
    assert (await _state(client, pod.session_id))["reason"] == "closed", "the older socket closed nothing"


# ───────────────────────── how it ends ─────────────────────────


@pytest.mark.parametrize(
    ("closed", "code", "reason"),
    [
        ((4000, "done"), 4000, "agent_closed"),
        ((1000, ""), 1000, "agent_closed"),
        ((1008, "budget_exceeded"), 1008, "budget_exceeded"),
        ((1006, "lost"), 1011, "agent_closed"),
    ],
)
async def test_the_containers_close_closes_the_callers_socket_with_its_code(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
    registry: Any,  # noqa: F811
    temporal: Any,  # noqa: F811
    pod_http: AsyncClient,  # noqa: F811
    quick: None,  # noqa: F811
    closed: tuple[int, str],
    code: int,
    reason: str,
) -> None:
    socket, pod = await _opened(client, pod_http, close=closed, close_after=1)
    running = asyncio.create_task(pod.run())
    await socket.send_text("bye")
    assert await socket.receive_json() == _echo("bye"), "the last frame before the close"
    ended = await socket.closed()
    await asyncio.wait_for(running, 5)
    assert ended.code == code
    assert (await _state(client, pod.session_id))["reason"] == reason


@pytest.mark.parametrize(
    ("message", "code"),
    [({"type": "websocket.receive", "bytes": b"\x00\x01"}, 1003), (None, 1009)],
)
async def test_a_frame_the_relay_cannot_carry_closes_the_session(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
    registry: Any,  # noqa: F811
    temporal: Any,  # noqa: F811
    pod_http: AsyncClient,  # noqa: F811
    quick: None,  # noqa: F811
    message: dict[str, Any] | None,
    code: int,
) -> None:
    from choregos_api.schemas.agent_sessions import SOCKET_FRAME_BYTES

    socket, pod = await _opened(client, pod_http)
    if message is None:
        await socket.send_text("x" * (SOCKET_FRAME_BYTES + 1))
    else:
        await socket._to_app.put(message)
    ended = await socket.closed()
    assert ended.code == code
    assert (await _state(client, pod.session_id))["status"] != "open"
    journal = [event["type"] for event in await _events(pod.session_id)]
    assert "socket.in" not in journal, "nothing of it reached the pod"


async def test_a_pod_that_cannot_start_closes_the_socket(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
    registry: Any,  # noqa: F811
    temporal: Any,  # noqa: F811
    pod_http: AsyncClient,  # noqa: F811
    quick: None,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refused(workflow_id: str, payload: dict[str, Any]) -> str:
        raise RuntimeError("temporal is down")

    monkeypatch.setattr(temporal, "start_session_pod", refused)
    await _hosted(client, protocols=["invocations_ws"])
    socket = _socket(client, SOCKET)
    await socket.__aenter__()
    session_id = await _session_id(socket)
    ended = await socket.closed()
    assert ended.code == 1011 and "could not start" in ended.reason
    assert (await _state(client, session_id))["reason"] == "hosted_unavailable"


async def test_an_agent_without_a_socket_is_refused_before_the_socket_opens(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
    registry: Any,  # noqa: F811
    temporal: Any,  # noqa: F811
) -> None:
    await _hosted(client, protocols=["responses"])
    with pytest.raises(Closed) as refused:
        async with _socket(client, SOCKET):
            pass
    assert refused.value.code == 1008
    assert temporal.start_calls == [] and upstream.gateway.keys == {}
