"""`serve-session` (S41-11): the runner of a hosted session's pod answers each turn through the agent's
container, with a step's walls — the model through the loopback (the session's key, `max_turns` calls
per turn), the session's tools on `/mcp/choregos` judged by the guard rails then sent to the API —
and stops when the API says the session ended.

The API is a `FakeSessionApi` behind the real `SessionClient`; the container a `FakeHostedAgent` that
reaches the runner over real loopback sockets with nothing but its environment.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from choregos_adapters.fakes import FakeHostedAgent, FakeModelServer
from choregos_contracts import AgentRef, HostedCall, ModelRef, StageInput
from choregos_core.hosted import sidecar_env
from choregos_runner.exits import Exit
from choregos_runner.serve_session import SessionServer

from .conftest import loopback_ports

pytestmark = pytest.mark.asyncio

#: Built at run time: a literal that looks like a key would trip the secrets scan of the CI.
KEY = "-".join(["sk", "session", "5d2b" * 4])
TOKEN = "session-token-" + "e" * 16
IMAGE = "registry.example/acme/triage@sha256:" + "a" * 64
SESSION = "s1"
API = "http://api.internal/api/v1/internal"


@dataclass
class FakeSessionApi:
    """`/internal/agent-sessions/{id}`: the turns to hand out, then 410 once they are answered."""

    stage_input: StageInput
    turns: list[dict[str, Any]] = field(default_factory=list)
    tools: dict[str, str] = field(default_factory=lambda: {"search_tickets": "3 tickets found."})
    posted: dict[str, dict[str, Any]] = field(default_factory=dict)
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    tokens: set[str] = field(default_factory=set)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.tokens.add(request.headers.get("authorization", ""))
        prefix = f"/api/v1/internal/agent-sessions/{SESSION}"
        path = request.url.path.removeprefix(prefix)
        if path == "/input":
            return httpx.Response(200, json=self.stage_input.model_dump(mode="json", by_alias=True))
        if path == "/turns/next":
            if self.turns:
                return httpx.Response(200, json=self.turns.pop(0))
            return httpx.Response(410, json={"detail": "session s1 is closed (closed)"})
        if path.startswith("/turns/"):
            self.posted[path.removeprefix("/turns/")] = json.loads(request.content)
            return httpx.Response(200, json={"status": "recorded"})
        if path == "/tools":
            tools = [{"name": name, "inputSchema": {"type": "object"}} for name in self.tools]
            return httpx.Response(200, json={"tools": tools})
        if path.startswith("/tools/"):
            name = path.removeprefix("/tools/")
            if name not in self.tools:
                return httpx.Response(404, json={"detail": "no such tool"})
            self.tool_calls.append((name, json.loads(request.content)["arguments"]))
            return httpx.Response(200, json={"text": self.tools[name]})
        return httpx.Response(404)


def _session_input(stage_input: StageInput, **hosted: Any) -> StageInput:
    return stage_input.model_copy(
        update={
            "run_id": f"session-{SESSION}",
            "repo": None,
            "agent": AgentRef(backend="hosted"),
            "model": ModelRef(litellm_model="platform/standard", base_url="http://gateway:4000/v1"),
            "gateway_key": None,
            "hosted": HostedCall(image=IMAGE, agent_slug="triage", agent_version=3, **hosted),
            "budget": stage_input.budget.model_copy(update={"max_turns": 2}),
        }
    )


def _turn(turn_id: str, text: str, protocol: str = "responses") -> dict[str, Any]:
    return {"turn_id": turn_id, "input": text, "protocol": protocol}


def _server(
    api: FakeSessionApi,
    agent: FakeHostedAgent,
    settings: Any,
    model: FakeModelServer | None = None,
) -> SessionServer:
    settings.run_id = f"session-{SESSION}"
    settings.api_url = API
    settings.run_token = TOKEN
    settings.gateway_key = KEY
    server = SessionServer(settings)
    server.api_transport = api.transport()
    server.hosted_transport = agent.transport()
    server.model_transport = (model or FakeModelServer(key=KEY)).transport()
    server.loopback_ports = loopback_ports()
    server.readiness_interval = 0.05
    server.poll_s = 0
    hosted = api.stage_input.hosted
    assert hosted is not None
    agent.env = sidecar_env(
        hosted,
        run_id=api.stage_input.run_id,
        model=api.stage_input.model.litellm_model,
        mcp_servers=["choregos"],
        ports=server.loopback_ports,
    )
    return server


async def _serve(
    api: FakeSessionApi, agent: FakeHostedAgent, settings: Any, model: FakeModelServer | None = None
) -> Exit:
    return await _server(api, agent, settings, model).serve()


async def test_each_turn_goes_to_the_container_and_its_answer_back_to_the_api(
    stage_input: StageInput, runner_settings: Any
) -> None:
    model = FakeModelServer(key=KEY).say("one").say("two")
    agent = FakeHostedAgent(script=[("model", "triage this")], answer="Ticket 42 is a bug.")
    api = FakeSessionApi(
        _session_input(stage_input), turns=[_turn("resp_a", "triage 42"), _turn("resp_b", "and 43?")]
    )
    code = await _serve(api, agent, runner_settings, model)

    assert code is Exit.OK, "the session ended (410): the pod stops cleanly"
    assert [request["input"] for request in agent.requests] == ["triage 42", "and 43?"]
    assert set(api.posted) == {"resp_a", "resp_b"}
    answer = api.posted["resp_a"]
    assert (answer["status"], answer["text"], answer["turns"]) == ("answered", "Ticket 42 is a bug.", 1)
    assert answer["response_id"] == "resp_1"
    headers, body = model.requests[0]
    assert headers["authorization"] == f"Bearer {KEY}", "the session's key, held by the runner"
    assert body["model"] == "platform/standard"
    assert api.tokens == {f"Bearer {TOKEN}"}, "the API is called with the session's token only"
    assert all(value not in agent.env.values() for value in (KEY, TOKEN))


async def test_max_turns_counts_per_turn_not_per_session(
    stage_input: StageInput, runner_settings: Any
) -> None:
    model = FakeModelServer(key=KEY)
    agent = FakeHostedAgent(script=[("model", "a"), ("model", "b"), ("model", "c")], answer="Done.")
    api = FakeSessionApi(_session_input(stage_input), turns=[_turn("t1", "one"), _turn("t2", "two")])
    await _serve(api, agent, runner_settings, model)

    assert len(model.requests) == 4, "two calls per turn reach the gateway, the third is refused"
    assert [status for status, _ in agent.seen] == [200, 200, 400] * 2
    for turn in ("t1", "t2"):
        assert api.posted[turn]["turns"] == 2
        refused = [e for e in api.posted[turn]["events"] if e["type"] == "hosted.model_refused"]
        assert [e["payload"]["reason"] for e in refused] == ["max_turns"]


async def test_the_container_reaches_the_session_tools_through_the_guard_rails_and_the_api(
    stage_input: StageInput, runner_settings: Any
) -> None:
    agent = FakeHostedAgent(
        script=[
            ("mcp", "choregos", "search_tickets", {"query": "login"}),
            ("mcp", "choregos", "delete_everything", {}),
            ("mcp", "crm", "lookup", {}),
        ],
        answer="Found them.",
    )
    api = FakeSessionApi(_session_input(stage_input), turns=[_turn("t1", "find login tickets")])
    await _serve(api, agent, runner_settings)

    granted, unknown, other = agent.seen
    assert granted[0] == 200 and granted[1]["result"]["content"][0]["text"] == "3 tickets found."
    assert unknown[1]["result"]["isError"] and "no tool named" in unknown[1]["result"]["content"][0]["text"]
    assert other[0] == 404, "the session's pod serves its own tools, no other MCP server"
    assert api.tool_calls == [("search_tickets", {"query": "login"})]
    answer = api.posted["t1"]
    assert answer["tool_calls"] == 2
    judged = [e["payload"]["tool"] for e in answer["events"] if e["type"] == "session/request_permission"]
    assert judged == ["choregos/search_tickets", "choregos/delete_everything"], "each call is judged"


async def test_a_call_the_guard_rails_refuse_never_reaches_the_api(
    stage_input: StageInput, runner_settings: Any
) -> None:
    agent = FakeHostedAgent(script=[("mcp", "choregos", "search_tickets", {"command": "rm -rf /"})])
    api = FakeSessionApi(_session_input(stage_input), turns=[_turn("t1", "x")])
    await _serve(api, agent, runner_settings)

    ((status, answer),) = agent.seen
    assert status == 200 and answer["result"]["isError"]
    assert "guard rails" in answer["result"]["content"][0]["text"]
    assert api.tool_calls == []


async def test_a_container_never_ready_fails_each_turn_not_ready(
    stage_input: StageInput, runner_settings: Any
) -> None:
    agent = FakeHostedAgent(ready_after=None)
    api = FakeSessionApi(
        _session_input(stage_input, readiness_timeout_s=1), turns=[_turn("t1", "x"), _turn("t2", "y")]
    )
    code = await _serve(api, agent, runner_settings)

    assert code is Exit.OK
    assert [api.posted[t]["status"] for t in ("t1", "t2")] == ["not_ready", "not_ready"]
    assert agent.requests == [], "nothing is sent to a container that never said it was ready"


async def test_an_invocations_turn_and_a_failed_response(
    stage_input: StageInput, runner_settings: Any
) -> None:
    agent = FakeHostedAgent(answer="Fine.")
    api = FakeSessionApi(_session_input(stage_input), turns=[_turn("t1", "x", protocol="invocations")])
    await _serve(api, agent, runner_settings)
    assert (api.posted["t1"]["status"], api.posted["t1"]["text"]) == ("answered", "Fine.")

    failing = FakeHostedAgent(fail=True)
    api = FakeSessionApi(_session_input(stage_input), turns=[_turn("t2", "x")])
    await _serve(api, failing, runner_settings)
    assert api.posted["t2"]["status"] == "failed" and "the agent crashed" in api.posted["t2"]["error"]


async def test_a_session_already_ended_starts_nothing(stage_input: StageInput, runner_settings: Any) -> None:
    agent = FakeHostedAgent()
    api = FakeSessionApi(_session_input(stage_input))
    code = await _serve(api, agent, runner_settings)

    assert code is Exit.OK and api.posted == {} and agent.requests == []


async def test_a_run_id_that_is_not_a_sessions_is_refused(runner_settings: Any) -> None:
    runner_settings.run_id = "run-test-1"
    asked: list[httpx.Request] = []
    server = SessionServer(runner_settings)
    server.api_transport = httpx.MockTransport(lambda request: asked.append(request) or httpx.Response(500))
    assert await server.serve() is Exit.INPUT_NOT_FOUND
    assert asked == [], "a run's token never asks for a session's turns"


async def test_a_stop_ends_the_wait_for_a_turn(stage_input: StageInput, runner_settings: Any) -> None:
    import asyncio

    class Waiting(FakeSessionApi):
        async def handle(self, request: httpx.Request) -> httpx.Response:  # type: ignore[override]
            if request.url.path.endswith("/turns/next"):
                await asyncio.sleep(60)  # a long poll no turn ends
                return httpx.Response(204)
            return super().handle(request)

    server = _server(Waiting(_session_input(stage_input)), FakeHostedAgent(), runner_settings)
    serving = asyncio.ensure_future(server.serve())
    await asyncio.sleep(0.5)
    assert not serving.done(), "with no turn and no end, the pod waits"
    server.stop()  # what SIGTERM does
    assert await asyncio.wait_for(serving, 5) is Exit.OK, "a stop does not wait for the long poll"
