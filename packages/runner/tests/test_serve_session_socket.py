"""`serve-session` relays the caller's socket to the container's `/invocations_ws` (S41-21).

The caller's frames come from `…/socket/next` (`open`, `in`, `close`); what the container sends back
is posted to `…/socket/out`, numbered after the journal's last frame, so a post sent again changes
nothing. The API is a `FakeSocketApi` behind the real `SessionClient`; the container a
`FakeHostedAgent`, whose socket is the runner's connector.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from choregos_adapters.fakes import FakeHostedAgent, FakeModelServer
from choregos_contracts import StageInput
from choregos_runner.exits import Exit

from .test_serve_session import KEY, SESSION, FakeSessionApi, _server, _session_input

pytestmark = pytest.mark.asyncio

ECHO = json.dumps({"type": "echo", "text": "hi"})


@dataclass
class FakeSocketApi(FakeSessionApi):
    """No turns; the caller's frames to hand out, and the container's as the journal keeps them.
    The session ends (410) once the container's close is posted, once the caller's `close` is read,
    or once `ends_after` frames are."""

    frames: list[dict[str, Any]] = field(default_factory=list)
    out: dict[int, str] = field(default_factory=dict)
    posts: list[dict[str, Any]] = field(default_factory=list)
    closed: dict[str, Any] | None = None
    #: The first posts to `socket/out` answered 500 (the API restarting).
    failing_posts: int = 0
    #: The session ends once the container sent this many frames.
    ends_after: int | None = None
    asked: list[int] = field(default_factory=list)
    caller_closed: bool = False

    @property
    def ended(self) -> bool:
        spent = self.ends_after is not None and len(self.out) >= self.ends_after
        return self.closed is not None or self.caller_closed or spent

    async def handle(self, request: httpx.Request) -> httpx.Response:  # type: ignore[override]
        path = request.url.path.removeprefix(f"/api/v1/internal/agent-sessions/{SESSION}")
        if path == "/turns/next":
            if self.ended:
                return httpx.Response(410, json={"detail": "the session ended"})
            await asyncio.sleep(0.02)
            return httpx.Response(204)
        if path == "/socket/next":
            after = int(request.url.params["after"])
            self.asked.append(after)
            if self.ended:
                return httpx.Response(410, json={"detail": "the session ended"})
            frames = [frame for frame in self.frames if frame["seq"] > after]
            if not frames:
                await asyncio.sleep(0.02)
            self.caller_closed = self.caller_closed or any(frame["kind"] == "close" for frame in frames)
            cursor = max([after, *(frame["seq"] for frame in self.frames)])
            return httpx.Response(200, json={"frames": frames, "cursor": cursor, "out_n": len(self.out)})
        if path == "/socket/out":
            if self.ended:
                return httpx.Response(410, json={"detail": "the session ended"})
            body = json.loads(request.content)
            self.posts.append(body)
            if self.failing_posts:
                self.failing_posts -= 1
                return httpx.Response(500, json={"detail": "restarting"})
            for frame in body["frames"]:
                if frame["n"] > len(self.out) + 1:
                    return httpx.Response(409, json={"detail": "out of order"})
                self.out.setdefault(frame["n"], frame["text"])
            self.closed = self.closed or body["closed"]
            return httpx.Response(200, json={"out_n": len(self.out)})
        return super().handle(request)


def _frames(*texts: str, close: bool = False) -> list[dict[str, Any]]:
    frames = [{"seq": 1, "kind": "open", "text": "", "code": None}]
    frames += [{"seq": 2 + i, "kind": "in", "text": text, "code": None} for i, text in enumerate(texts)]
    if close:
        frames.append({"seq": 2 + len(texts), "kind": "close", "text": "", "code": 1000})
    return frames


async def _relay(
    api: FakeSocketApi, agent: FakeHostedAgent, settings: Any, model: FakeModelServer | None = None
) -> Exit:
    server = _server(api, agent, settings, model)
    server.socket_connector = agent
    server.socket_poll_s = 0
    return await asyncio.wait_for(server.serve(), 10)


def _socket_input(stage_input: StageInput) -> StageInput:
    return _session_input(stage_input, socket=True)


async def test_the_callers_frames_reach_the_containers_socket_and_its_frames_come_back_numbered(
    stage_input: StageInput, runner_settings: Any
) -> None:
    agent = FakeHostedAgent(socket_greeting=["hello"], socket_close=(4000, "done"), socket_close_after=2)
    api = FakeSocketApi(_socket_input(stage_input), frames=_frames("hi", "there"))
    assert await _relay(api, agent, runner_settings) is Exit.OK

    hosted = api.stage_input.hosted
    assert hosted is not None
    assert agent.socket_urls == [f"ws://127.0.0.1:{hosted.port}/invocations_ws"], "opened once"
    assert agent.socket_heard == ["hi", "there"]
    assert api.out == {1: "hello", 2: ECHO, 3: json.dumps({"type": "echo", "text": "there"})}
    assert api.closed == {"code": 4000, "reason": "done"}, "the container's close, for the caller"


async def test_the_numbering_follows_the_journal(stage_input: StageInput, runner_settings: Any) -> None:
    agent = FakeHostedAgent(socket_close=(1000, ""), socket_close_after=1)
    api = FakeSocketApi(_socket_input(stage_input), frames=_frames("hi"), out={1: "a", 2: "b"})
    await _relay(api, agent, runner_settings)
    assert api.out == {1: "a", 2: "b", 3: ECHO}, "the next frame is numbered after the journal's last"


async def test_a_post_the_api_did_not_take_is_sent_again_with_its_number(
    stage_input: StageInput, runner_settings: Any
) -> None:
    agent = FakeHostedAgent(socket_close=(1000, ""), socket_close_after=1)
    api = FakeSocketApi(_socket_input(stage_input), frames=_frames("hi"), failing_posts=2)
    await _relay(api, agent, runner_settings)
    assert [post["frames"] for post in api.posts[:3]] == [[{"n": 1, "text": ECHO}]] * 3
    assert api.out == {1: ECHO}


async def test_a_close_without_a_status_is_a_normal_close(
    stage_input: StageInput, runner_settings: Any
) -> None:
    agent = FakeHostedAgent(socket_close=(1005, ""), socket_close_after=0)
    api = FakeSocketApi(_socket_input(stage_input), frames=_frames())
    await _relay(api, agent, runner_settings)
    assert api.closed == {"code": 1000, "reason": ""}


async def test_the_callers_close_closes_the_containers_socket(
    stage_input: StageInput, runner_settings: Any
) -> None:
    agent = FakeHostedAgent()
    api = FakeSocketApi(_socket_input(stage_input), frames=_frames("hi", close=True))
    await _relay(api, agent, runner_settings)
    assert agent.socket_heard == ["hi"]
    assert agent.sockets[0].closed
    assert api.asked == [0], "the caller's close ends the relay: it reads no frame after it"
    assert api.closed is None, "the caller closed it: the API closes the session"


async def test_each_frame_is_a_turn_for_max_turns(stage_input: StageInput, runner_settings: Any) -> None:
    model = FakeModelServer(key=KEY)
    agent = FakeHostedAgent(script=[("model", "a"), ("model", "b"), ("model", "c")])
    api = FakeSocketApi(_socket_input(stage_input), frames=_frames("one", "two"), ends_after=2)
    await _relay(api, agent, runner_settings, model)

    assert len(model.requests) == 4, "two calls per frame reach the gateway, the third is refused"
    assert [status for status, _ in agent.seen] == [200, 200, 400] * 2
    events = [event for post in api.posts for event in post["events"]]
    refused = [event for event in events if event["type"] == "hosted.model_refused"]
    assert [event["payload"]["reason"] for event in refused] == ["max_turns", "max_turns"]


async def test_a_spent_key_closes_the_socket_on_budget(stage_input: StageInput, runner_settings: Any) -> None:
    refusal = {"error": {"message": "Budget has been exceeded! Current cost: 5.1", "type": "budget_exceeded"}}
    model = FakeModelServer(key=KEY).refuse(400, refusal)
    agent = FakeHostedAgent(script=[("model", "a")])
    api = FakeSocketApi(_socket_input(stage_input), frames=_frames("hi", "again"))
    await _relay(api, agent, runner_settings, model)

    assert api.closed == {"code": 1008, "reason": "budget_exceeded"}
    assert agent.socket_heard == ["hi"], "nothing more reaches the container"


async def test_a_socket_the_container_refuses_is_closed_for_the_caller(
    stage_input: StageInput, runner_settings: Any
) -> None:
    agent = FakeHostedAgent(socket_refused=True)
    api = FakeSocketApi(_socket_input(stage_input), frames=_frames("hi"))
    await _relay(api, agent, runner_settings)
    assert api.closed == {"code": 1011, "reason": "the agent's socket refused the connection"}
    events = [event["type"] for post in api.posts for event in post["events"]]
    assert "hosted.socket_refused" in events


async def test_a_container_never_ready_is_closed_for_the_caller(
    stage_input: StageInput, runner_settings: Any
) -> None:
    agent = FakeHostedAgent(ready_after=None)
    api = FakeSocketApi(_session_input(stage_input, socket=True, readiness_timeout_s=1), frames=_frames("hi"))
    await _relay(api, agent, runner_settings)
    assert api.closed == {"code": 1011, "reason": "the agent's container is not ready"}
    assert agent.socket_urls == []


async def test_a_pod_without_a_socket_never_reads_the_callers_frames(
    stage_input: StageInput, runner_settings: Any
) -> None:
    agent = FakeHostedAgent()
    api = FakeSocketApi(_session_input(stage_input), frames=_frames("hi"))
    asyncio.get_running_loop().call_later(0.3, setattr, api, "caller_closed", True)
    await _relay(api, agent, runner_settings)
    assert api.asked == [] and agent.socket_urls == []
