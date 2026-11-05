"""The tool bridge: a script calls its run's tools through the run's tool server (ADR 0122 §1, §4, §7).

The fake internal API below is an `httpx` transport: it sees what the bridge really sends (the run
token, the origin, the script's digest, the arguments' bytes) and answers what a test scripts, so
each test reads the bytes on both sides of the bridge.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import choregos_contracts
import httpx
import pytest
from choregos_contracts import StageInput, ToolScripts
from choregos_tools_mcp import McpServer
from choregos_tools_mcp import app as app_module
from choregos_tools_mcp.app import create_app
from choregos_tools_mcp.bridge import (
    CALL_EVENT,
    MAX_BODY_BYTES,
    MAX_SCRIPT_BYTES,
    SCRIPT_EVENT,
    SIDECAR_SEQ_BASE,
    ApiJournal,
    Bridge,
    StageInputBridge,
)
from choregos_tools_mcp.client import InternalClient

RUN = "run-42"
API = "http://api.test/api/v1/internal"
#: A run token as long as a real one: what must never leave the runner's side of the bridge.
TOKEN = "rt-7f3c9a1e5b2d4c6f8a0b1c2d3e4f5a6b"
TOOL = "pfsense__list_firewall_rules"
DIGEST = "ab" * 32
#: `getRunTools` as the API lists it: the platform's keys and the output schema the model is not shown.
TOOLS = (
    b'{"tools": [{"name": "pfsense__list_firewall_rules", "description": "[pfsense] Lists the rules", '
    b'"inputSchema": {"type": "object"}, "outputSchema": {"type": "object", "required": ["rules"]}, '
    b'"annotations": {"readOnlyHint": true}, "x-choregos-access": "read"}]}'
)
#: An answer of `callRunTool` as the API writes it: its own key order and spacing, kept byte for byte.
ANSWER = (
    b'{"status_code": 200, "result": {"content": [{"type": "text", "text": "3 rules \\u2014 wan"}],'
    b'"structuredContent":{"rules":[1,2,3]}},"remaining":7,'
    b'"suspicions":[{"source":"tool.pfsense__list_firewall_rules","motif":"prompt tags"}]}'
)
CAP = (
    b'{"type":"about:blank","title":"Tool call cap reached","status":429,'
    b'"detail":"this run has already called 10 tools (cap 10)"}'
)


class FakeApi:
    """The run's internal API: each request kept; `/tools/{name}` answers `answer`, after `gate`."""

    def __init__(self, scripts: ToolScripts | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.events: list[dict[str, Any]] = []
        self.answer = httpx.Response(200, content=ANSWER, headers={"content-type": "application/json"})
        #: Each tool call waits for a release when set: the test lets them through one at a time.
        self.gate: asyncio.Semaphore | None = None
        self.arrived = 0
        self.in_flight = 0
        self.most_in_flight = 0
        self.slow = 0.0
        self.unreachable = False
        self.scripts = scripts
        self.input_fails = 0
        self.inputs_read = 0

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.unreachable:
            raise httpx.ConnectError("connection refused", request=request)
        path = request.url.path.removeprefix(f"/api/v1/internal/runs/{RUN}")
        if path == "/tools":
            return httpx.Response(200, content=TOOLS, headers={"content-type": "application/json"})
        if path.startswith("/tools/"):
            self.arrived += 1
            self.in_flight += 1
            self.most_in_flight = max(self.most_in_flight, self.in_flight)
            try:
                if self.gate is not None:
                    await self.gate.acquire()
                if self.slow:
                    await asyncio.sleep(self.slow)
                return self.answer
            finally:
                self.in_flight -= 1
        if path == "/events":
            events = json.loads(request.content)["events"]
            self.events.extend(events)
            return httpx.Response(202, json={"accepted": len(events)})
        if path == "/input":
            self.inputs_read += 1
            if self.input_fails:
                self.input_fails -= 1
                return httpx.Response(503, json={"title": "down"})
            return httpx.Response(200, json=_stage_input(self.scripts))
        return httpx.Response(404, json={"detail": "Not Found"})


def _stage_input(scripts: ToolScripts | None) -> dict[str, Any]:
    example = choregos_contracts.schemas_dir() / "examples" / "stage-input.example.json"
    stage_input = StageInput.model_validate(json.loads(example.read_text(encoding="utf-8")))
    stage_input.tools.scripts = scripts
    return stage_input.model_dump(mode="json", by_alias=True, exclude_none=True)


class Journal:
    """The runner's journal, reduced to what the bridge writes into it."""

    def __init__(self) -> None:
        self.lines: list[tuple[str, dict[str, Any]]] = []

    async def record(self, type_: str, payload: dict[str, Any]) -> None:
        self.lines.append((type_, payload))

    def of(self, type_: str) -> list[dict[str, Any]]:
        return [payload for kind, payload in self.lines if kind == type_]


Bridged = tuple[httpx.AsyncClient, FakeApi, Journal]


def _client(api: FakeApi) -> InternalClient:
    return InternalClient(API, RUN, TOKEN, transport=httpx.MockTransport(api.handle))


@pytest.fixture
def make_bridged() -> Callable[..., Awaitable[Bridged]]:
    """The runner's tool server with a bridge in front of a fake API, and an HTTP client for it."""
    opened: list[httpx.AsyncClient] = []

    async def make(max_concurrent_calls: int = 4, call_timeout: float = 60.0) -> Bridged:
        api, journal = FakeApi(), Journal()
        client = _client(api)
        bridge = Bridge(
            client,
            ToolScripts(max_concurrent_calls=max_concurrent_calls),
            journal,
            secrets=(TOKEN,),
            call_timeout=call_timeout,
        )
        application = create_app(McpServer(client), bridge=bridge)
        http = httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://127.0.0.1")
        opened.append(http)
        return http, api, journal

    return make


@pytest.fixture
async def bridged(make_bridged: Callable[..., Awaitable[Bridged]]) -> AsyncIterator[Bridged]:
    http, api, journal = await make_bridged()
    async with http:
        yield http, api, journal


async def test_a_call_is_relayed_with_the_run_token_the_origin_and_the_script(bridged: Bridged) -> None:
    http, api, journal = bridged
    arguments = b'{"interface": "wan",  "limit": 5}'
    response = await http.post(
        f"/bridge/v1/tools/{TOOL}", content=arguments, headers={"X-Choregos-Script": DIGEST}
    )

    [sent] = api.requests
    assert (sent.method, sent.url.path) == ("POST", f"/api/v1/internal/runs/{RUN}/tools/{TOOL}")
    assert sent.headers["authorization"] == f"Bearer {TOKEN}", "the runner's run token, not the agent's"
    assert sent.headers["x-choregos-tool-origin"] == "script"
    assert sent.headers["x-choregos-script"] == DIGEST
    assert sent.content == arguments, "the arguments as the script sent them"
    assert response.status_code == 200
    assert response.content == ANSWER, "the API's answer, byte for byte: status_code, remaining, suspicions"
    assert response.headers["content-type"] == "application/json"
    [call] = journal.of(CALL_EVENT)
    assert call["tool"] == TOOL and call["status_code"] == 200 and call["script_sha256"] == DIGEST
    assert call["response_bytes"] == len(ANSWER) and isinstance(call["duration_ms"], int)


async def test_a_call_without_a_digest_is_still_a_script_s(bridged: Bridged) -> None:
    http, api, journal = bridged
    response = await http.post(f"/bridge/v1/tools/{TOOL}")

    [sent] = api.requests
    assert sent.headers["x-choregos-tool-origin"] == "script" and "x-choregos-script" not in sent.headers
    assert sent.content == b"{}", "no body is no arguments"
    assert response.status_code == 200
    assert journal.of(CALL_EVENT)[0]["script_sha256"] is None


async def test_the_origin_is_the_bridge_s_to_say_not_the_caller_s(bridged: Bridged) -> None:
    http, api, _ = bridged
    await http.post(
        f"/bridge/v1/tools/{TOOL}",
        json={},
        headers={"X-Choregos-Tool-Origin": "job", "Authorization": "Bearer stolen"},
    )
    [sent] = api.requests
    assert sent.headers["x-choregos-tool-origin"] == "script"
    assert sent.headers["authorization"] == f"Bearer {TOKEN}"


async def test_a_problem_of_the_api_is_handed_back_as_it_came(bridged: Bridged) -> None:
    http, api, journal = bridged
    api.answer = httpx.Response(
        429, content=CAP, headers={"content-type": "application/problem+json", "retry-after": "30"}
    )
    response = await http.post(f"/bridge/v1/tools/{TOOL}", json={})

    assert response.status_code == 429, "the client of S57-08 reads 429 as CapReached"
    assert response.content == CAP
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["retry-after"] == "30"
    assert journal.of(CALL_EVENT)[0]["status_code"] == 429

    unknown = (
        b'{"type":"about:blank","title":"Tool not found","status":404,"detail":"Tool `x` does not exist"}'
    )
    api.answer = httpx.Response(404, content=unknown, headers={"content-type": "application/problem+json"})
    missing = await http.post("/bridge/v1/tools/x", json={})
    assert (missing.status_code, missing.content) == (404, unknown)


async def test_a_proposal_is_journaled_with_the_answer_s_own_status(bridged: Bridged) -> None:
    http, api, journal = bridged
    proposal = b'{"status_code":202,"result":{"action":"act-1","status":"pending_approval"},"remaining":null}'
    api.answer = httpx.Response(200, content=proposal, headers={"content-type": "application/json"})
    response = await http.post(f"/bridge/v1/tools/{TOOL}", json={"id": "r1"})

    assert (response.status_code, response.content) == (200, proposal)
    assert journal.of(CALL_EVENT)[0]["status_code"] == 202
    # The console links a script's proposal to its action from this line (#1369).
    assert journal.of(CALL_EVENT)[0]["action"] == "act-1"


async def test_only_a_proposal_s_own_action_id_is_journaled(bridged: Bridged) -> None:
    """A read whose result happens to carry an `action` key is not a proposal, and an action id that
    is not one the API mints never reaches the console's link (#1369)."""
    http, api, journal = bridged
    for answer in (
        b'{"status_code":200,"result":{"action":"act-2"},"remaining":null}',
        b'{"status_code":202,"result":{"action":"https://elsewhere.example/x"},"remaining":null}',
        b'{"status_code":202,"result":{"action":["act-3"]},"remaining":null}',
        b'{"status_code":202,"result":"accepted","remaining":null}',
    ):
        api.answer = httpx.Response(200, content=answer, headers={"content-type": "application/json"})
        await http.post(f"/bridge/v1/tools/{TOOL}", json={})
    assert [call.get("action") for call in journal.of(CALL_EVENT)] == [None, None, None, None]


async def test_the_tool_list_is_the_api_s_whole(bridged: Bridged) -> None:
    http, api, journal = bridged
    response = await http.get("/bridge/v1/tools")

    [sent] = api.requests
    assert (sent.method, sent.url.path) == ("GET", f"/api/v1/internal/runs/{RUN}/tools")
    assert sent.headers["authorization"] == f"Bearer {TOKEN}"
    assert response.status_code == 200 and response.content == TOOLS
    [tool] = response.json()["tools"]
    assert {"outputSchema", "x-choregos-access", "inputSchema"} <= set(tool), "not stripped for a script"
    assert journal.lines == [], "a listing is not a call"


async def test_the_next_call_waits_until_one_finishes(
    make_bridged: Callable[..., Awaitable[Bridged]],
) -> None:
    http, api, journal = await make_bridged(max_concurrent_calls=2)
    api.gate = asyncio.Semaphore(0)
    async with http:
        calls = [asyncio.create_task(http.post(f"/bridge/v1/tools/{TOOL}", json={"n": n})) for n in range(3)]
        await _until(lambda: api.arrived == 2)
        for _ in range(50):  # the third had every chance to leave
            await asyncio.sleep(0)
        assert api.arrived == 2, "the third call waits for a place, it does not reach the API"
        assert not any(call.done() for call in calls), "and it is not refused either"

        api.gate.release()
        await _until(lambda: api.arrived == 3)
        api.gate.release()
        api.gate.release()
        responses = await asyncio.gather(*calls)

    assert [r.status_code for r in responses] == [200, 200, 200]
    assert api.most_in_flight == 2
    assert len(journal.of(CALL_EVENT)) == 3


async def test_a_call_longer_than_its_bound_is_a_504(make_bridged: Callable[..., Awaitable[Bridged]]) -> None:
    http, api, journal = await make_bridged(call_timeout=0.05)
    api.slow = 1.0
    async with http:
        response = await http.post(f"/bridge/v1/tools/{TOOL}", json={})
    assert response.status_code == 504
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["title"] == "Tool call timed out"
    assert journal.of(CALL_EVENT)[0]["status_code"] == 504


async def test_an_unreachable_api_is_a_502(bridged: Bridged) -> None:
    http, api, journal = bridged
    api.unreachable = True
    response = await http.post(f"/bridge/v1/tools/{TOOL}", json={})
    listing = await http.get("/bridge/v1/tools")
    assert (response.status_code, listing.status_code) == (502, 502)
    assert journal.of(CALL_EVENT)[0]["status_code"] == 502


async def test_a_body_over_one_mebibyte_is_refused_unread(bridged: Bridged) -> None:
    http, api, journal = bridged
    huge = b'{"x": "' + b"y" * MAX_BODY_BYTES + b'"}'
    response = await http.post(f"/bridge/v1/tools/{TOOL}", content=huge)
    assert response.status_code == 413
    assert response.headers["content-type"] == "application/problem+json"
    assert api.requests == [] and journal.lines == []

    # A declared length over the bound is refused before a byte of the body is read.
    declared = await http.post(
        f"/bridge/v1/tools/{TOOL}", content=b"{}", headers={"content-length": str(MAX_BODY_BYTES + 1)}
    )
    assert declared.status_code == 413
    assert api.requests == []

    async def chunks() -> AsyncIterator[bytes]:  # no Content-Length: counted as it comes
        for _ in range(MAX_BODY_BYTES // 65536 + 2):
            yield b"z" * 65536

    streamed = await http.post("/bridge/v1/scripts", content=chunks())
    assert streamed.status_code == 413
    assert api.requests == []


async def test_a_malformed_script_digest_is_refused(bridged: Bridged) -> None:
    http, api, _ = bridged
    for digest in ("AB" * 32, "ab" * 31, "not-a-digest"):
        response = await http.post(f"/bridge/v1/tools/{TOOL}", json={}, headers={"X-Choregos-Script": digest})
        assert response.status_code == 400, digest
    assert api.requests == []


async def test_a_script_is_journaled_once_per_digest(bridged: Bridged) -> None:
    http, _, journal = bridged
    text = "from choregos_tools import pfsense\nprint(len(pfsense.list_firewall_rules()['rules']))\n"
    digest = hashlib.sha256(text.encode()).hexdigest()

    first = await http.post("/bridge/v1/scripts", json={"sha256": digest, "text": text})
    again = await http.post("/bridge/v1/scripts", json={"sha256": digest, "text": text})

    assert (first.status_code, again.status_code) == (201, 200)
    assert first.json() == {"sha256": digest, "bytes": len(text.encode()), "journaled": True}
    assert again.json()["journaled"] is False
    assert journal.of(SCRIPT_EVENT) == [{"sha256": digest, "bytes": len(text.encode()), "text": text}]


async def test_a_script_whose_digest_does_not_match_is_refused(bridged: Bridged) -> None:
    http, _, journal = bridged
    text = "print('hello')\n"
    wrong = hashlib.sha256(b"print('other')\n").hexdigest()
    big = "#" * (MAX_SCRIPT_BYTES + 1)
    refused = [
        await http.post("/bridge/v1/scripts", json={"sha256": wrong, "text": text}),
        await http.post(
            "/bridge/v1/scripts",
            json={"sha256": hashlib.sha256(text.encode()).hexdigest().upper(), "text": text},
        ),
        await http.post(
            "/bridge/v1/scripts", json={"sha256": hashlib.sha256(big.encode()).hexdigest(), "text": big}
        ),
        await http.post("/bridge/v1/scripts", json={"text": text}),
        await http.post("/bridge/v1/scripts", content=b"not json"),
    ]
    assert [r.status_code for r in refused] == [400] * 5
    assert all(r.headers["content-type"] == "application/problem+json" for r in refused)
    assert journal.of(SCRIPT_EVENT) == []


async def test_the_run_token_is_in_no_answer_and_no_journal_line(
    make_bridged: Callable[..., Awaitable[Bridged]],
) -> None:
    http, api, journal = await make_bridged(call_timeout=0.05)
    seen: list[bytes] = []
    async with http:
        seen.append((await http.get("/bridge/v1/tools")).content)
        seen.append((await http.post(f"/bridge/v1/tools/{TOOL}", json={})).content)
        api.answer = httpx.Response(429, content=CAP, headers={"content-type": "application/problem+json"})
        seen.append((await http.post(f"/bridge/v1/tools/{TOOL}", json={})).content)
        # An answer that would carry the token is handed back without it.
        leaking = json.dumps({"status_code": 200, "result": {"echo": f"Bearer {TOKEN}"}}).encode()
        api.answer = httpx.Response(200, content=leaking, headers={"content-type": "application/json"})
        seen.append((await http.post(f"/bridge/v1/tools/{TOOL}", json={})).content)
        api.slow = 1.0
        seen.append((await http.post(f"/bridge/v1/tools/{TOOL}", json={})).content)
        api.slow, api.unreachable = 0.0, True
        seen.append((await http.post(f"/bridge/v1/tools/{TOOL}", json={})).content)
        text = f"token = {TOKEN!r}\n"
        script = {"sha256": hashlib.sha256(text.encode()).hexdigest(), "text": text}
        seen.append((await http.post("/bridge/v1/scripts", json=script)).content)
        seen.append((await http.post("/bridge/v1/scripts", json={"sha256": "0" * 64, "text": text})).content)

    assert all(request.headers["authorization"] == f"Bearer {TOKEN}" for request in api.requests)
    assert len(seen) == 8 and all(TOKEN.encode() not in body for body in seen), seen
    assert b"[REDACTED]" in seen[3]
    lines = [json.dumps(line) for line in journal.lines]
    assert len(lines) == 6 and all(TOKEN not in line for line in lines), lines


# ───────────────────────── absent when scripts are off ─────────────────────────


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/bridge/v1/tools"), ("POST", f"/bridge/v1/tools/{TOOL}"), ("POST", "/bridge/v1/scripts")],
)
async def test_without_scripts_the_runner_s_server_has_no_bridge(method: str, path: str) -> None:
    api = FakeApi()
    application = create_app(McpServer(_client(api)))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://127.0.0.1"
    ) as http:
        response = await http.request(method, path, json={} if method == "POST" else None)
    assert response.status_code == 404
    assert api.requests == [], "nothing is relayed"
    assert not any(str(getattr(route, "path", "")).startswith("/bridge") for route in application.routes)


# ───────────────────────── the Tekton sidecar ─────────────────────────


def _sidecar(api: FakeApi, monkeypatch: pytest.MonkeyPatch) -> httpx.AsyncClient:
    """The sidecar's app as `main` serves it, its state as its lifespan sets it."""
    client = _client(api)
    monkeypatch.setattr(
        app_module,
        "_state",
        {
            "client": client,
            "server": McpServer(client),
            "bridge": StageInputBridge(client, ApiJournal(client), secrets=(TOKEN,)),
        },
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://127.0.0.1")


async def test_the_sidecar_reads_its_run_s_input_and_serves_the_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = FakeApi(scripts=ToolScripts(max_concurrent_calls=3))
    async with _sidecar(api, monkeypatch) as http:
        response = await http.post(f"/bridge/v1/tools/{TOOL}", json={}, headers={"X-Choregos-Script": DIGEST})
        await http.get("/bridge/v1/tools")
        await http.post(f"/bridge/v1/tools/{TOOL}", json={})

    assert (response.status_code, response.content) == (200, ANSWER)
    assert api.inputs_read == 1, "read once, with the run token"
    input_request = next(r for r in api.requests if r.url.path.endswith("/input"))
    assert input_request.headers["authorization"] == f"Bearer {TOKEN}"
    calls = [r for r in api.requests if "/tools/" in r.url.path]
    assert all(r.headers["x-choregos-tool-origin"] == "script" for r in calls)
    # Journaled through the internal API, numbered apart from the runner's own events.
    assert [e["type"] for e in api.events] == [CALL_EVENT, CALL_EVENT]
    assert api.events[0]["payload"]["script_sha256"] == DIGEST
    assert all(e["seq"] > SIDECAR_SEQ_BASE and e["ts"] for e in api.events)
    assert api.events[1]["seq"] > api.events[0]["seq"]


async def test_the_sidecar_has_no_bridge_when_its_run_is_not_offered_scripts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = FakeApi(scripts=None)
    async with _sidecar(api, monkeypatch) as http:
        answers = [
            await http.get("/bridge/v1/tools"),
            await http.post(f"/bridge/v1/tools/{TOOL}", json={}),
            await http.post("/bridge/v1/scripts", json={"sha256": DIGEST, "text": "x"}),
        ]
    assert [a.status_code for a in answers] == [404, 404, 404]
    assert all(a.json() == {"detail": "Not Found"} for a in answers), "as a route that does not exist"
    assert [r.url.path for r in api.requests] == [f"/api/v1/internal/runs/{RUN}/input"]
    assert api.events == []


async def test_an_unreadable_input_is_asked_again(monkeypatch: pytest.MonkeyPatch) -> None:
    api = FakeApi(scripts=ToolScripts())
    api.input_fails = 1
    async with _sidecar(api, monkeypatch) as http:
        first = await http.post(f"/bridge/v1/tools/{TOOL}", json={})
        second = await http.post(f"/bridge/v1/tools/{TOOL}", json={})
    assert (first.status_code, second.status_code) == (503, 200)
    assert api.inputs_read == 2


async def test_the_sidecar_s_lifespan_builds_its_bridge_from_its_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = FakeApi(scripts=ToolScripts())
    monkeypatch.setattr(app_module, "_state", {})
    monkeypatch.setenv("CHOREGOS_API_URL", API)
    monkeypatch.setenv("CHOREGOS_RUN_ID", RUN)
    monkeypatch.setenv("CHOREGOS_RUN_TOKEN", TOKEN)
    real = InternalClient

    def through_the_fake(base_url: str, run_id: str, token: str) -> InternalClient:
        return real(base_url, run_id, token, transport=httpx.MockTransport(api.handle))

    monkeypatch.setattr(app_module, "InternalClient", through_the_fake)
    application = create_app()
    async with (
        application.router.lifespan_context(application),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://127.0.0.1"
        ) as http,
    ):
        response = await http.post(f"/bridge/v1/tools/{TOOL}", json={})
    assert (response.status_code, response.content) == (200, ANSWER)
    assert api.requests[-1].headers["authorization"] == f"Bearer {TOKEN}"


async def test_the_sidecar_s_numbers_go_on_after_a_restart() -> None:
    api = FakeApi()
    now = [1_762_250_000.0]
    journal = ApiJournal(_client(api), clock=lambda: now[0])
    for _ in range(3):  # the same millisecond three times: three numbers all the same
        await journal.record(CALL_EVENT, {"n": 1})
    now[0] += 5.0
    restarted = ApiJournal(_client(api), clock=lambda: now[0])
    await restarted.record(CALL_EVENT, {"n": 2})

    seqs = [event["seq"] for event in api.events]
    assert len(set(seqs)) == 4 and seqs == sorted(seqs)
    assert all(SIDECAR_SEQ_BASE < seq < 2**31 for seq in seqs), "beyond any runner's, within an int4"

    api.unreachable = True
    await restarted.record(CALL_EVENT, {"n": 3})  # a lost line never raises


async def _until(condition: Callable[[], bool]) -> None:
    for _ in range(2000):
        if condition():
            return
        await asyncio.sleep(0.001)
    raise AssertionError("the condition never held")
