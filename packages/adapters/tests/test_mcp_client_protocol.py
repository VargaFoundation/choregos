"""The MCP client can talk to a gateway (S46-03): it offers 2026-07-28 and refuses a version it does
not speak, recovers a lost session once, and sends static and per-request headers.

Every server here is an inline `httpx.MockTransport` handler: the behaviours under test are what a
gateway or a strict server does, which the scriptable `FakeMcpServer` does not do."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from choregos_adapters.mcp import CLIENT_VERSIONS, McpClient, McpError
from choregos_core.mcp import negocier, version_acceptable

URL = "https://gateway.test/mcp"
TOOLS = [{"name": "read_order", "inputSchema": {"type": "object"}}]

#: (JSON-RPC method, lower-cased headers, body) of every request a handler received.
Seen = list[tuple[str, dict[str, str], dict[str, Any]]]
Handler = Callable[[httpx.Request], httpx.Response]


def _record(seen: Seen, request: httpx.Request) -> tuple[str, dict[str, str], dict[str, Any]]:
    body = json.loads(request.content or b"{}")
    entry = (str(body.get("method", "")), {k.lower(): v for k, v in request.headers.items()}, body)
    seen.append(entry)
    return entry


def _answer(body: dict[str, Any], result: dict[str, Any], session: str | None = None) -> httpx.Response:
    headers = {"Mcp-Session-Id": session} if session else {}
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result}, headers=headers)


def _initialized(body: dict[str, Any], version: str, session: str | None = None) -> httpx.Response:
    result = {"protocolVersion": version, "capabilities": {"tools": {}}, "serverInfo": {"name": "gateway"}}
    return _answer(body, result, session)


def _server(seen: Seen, *, version: str | None = None) -> Handler:
    """A plain server: it answers `version` to `initialize` (the offer when None), issues no session
    and lists `TOOLS`."""

    def handle(request: httpx.Request) -> httpx.Response:
        method, _, body = _record(seen, request)
        if "id" not in body:
            return httpx.Response(202)
        if method == "initialize":
            return _initialized(body, version or body["params"]["protocolVersion"])
        return _answer(body, {"tools": TOOLS})

    return handle


def _methods(seen: Seen) -> list[str]:
    return [method for method, _, _ in seen]


def _client(handler: Handler, **options: Any) -> McpClient:
    return McpClient(URL, transport=httpx.MockTransport(handler), **options)


async def test_initialize_offers_2026_07_28_without_a_version_header() -> None:
    seen: Seen = []
    await _client(_server(seen)).list_tools()
    method, headers, body = seen[0]
    assert method == "initialize"
    assert body["params"]["protocolVersion"] == "2026-07-28" == CLIENT_VERSIONS[0]
    assert "mcp-protocol-version" not in headers


async def test_the_agreed_version_travels_on_every_later_request() -> None:
    seen: Seen = []
    client = _client(_server(seen, version="2025-06-18"))
    assert client.negotiated_version is None
    await client.list_tools()
    assert _methods(seen) == ["initialize", "notifications/initialized", "tools/list"]
    for method, headers, _ in seen[1:]:
        assert headers["mcp-protocol-version"] == "2025-06-18", method
    assert client.negotiated_version == "2025-06-18"


async def test_a_server_that_speaks_only_today_s_versions_is_reached() -> None:
    """Choregos's own servers (the sidecar, the API's door) do not speak 2026-07-28: they refuse the
    header of a version they do not know, and answer an offer of it with their newest."""
    seen: Seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        method, headers, body = _record(seen, request)
        if not version_acceptable(headers.get("mcp-protocol-version")):
            return httpx.Response(400, json={"error": "unsupported protocol version"})
        if "id" not in body:
            return httpx.Response(202)
        if method == "initialize":
            return _initialized(body, negocier(body["params"]["protocolVersion"]))
        return _answer(body, {"tools": TOOLS})

    client = _client(handle)
    tools = await client.list_tools()
    assert [tool.name for tool in tools] == ["read_order"]
    assert client.negotiated_version == "2025-11-25"


async def test_a_version_the_client_does_not_speak_is_refused() -> None:
    seen: Seen = []
    client = _client(_server(seen, version="2023-01-01"))
    with pytest.raises(McpError, match="2023-01-01") as refused:
        await client.list_tools()
    assert "does not speak" in str(refused.value)
    assert _methods(seen) == ["initialize"], "nothing is sent in a version the client does not speak"
    assert client.negotiated_version is None


async def test_a_stateless_server_is_served_without_a_session() -> None:
    seen: Seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        method, headers, body = _record(seen, request)
        if "mcp-session-id" in headers:
            return httpx.Response(400, json={"error": "this server holds no session"})
        if "id" not in body:
            return httpx.Response(202)
        if method == "initialize":
            return _initialized(body, body["params"]["protocolVersion"])
        return _answer(body, {"tools": TOOLS})

    client = _client(handle)
    tools = await client.list_tools()
    assert [tool.name for tool in tools] == ["read_order"]
    assert client.negotiated_version == "2026-07-28"
    assert _methods(seen) == ["initialize", "notifications/initialized", "tools/list"]


def _gateway(seen: Seen, lost: Callable[[str, str | None], bool]) -> Handler:
    """A gateway in front of replicas. Each `initialize` opens a new session (`s1`, `s2`...) and is
    refused (400) when it carries a session id; a request is answered 404 when `lost(method, session)`
    says the replica it reached does not hold its session, as the MCP transport prescribes."""
    opened = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal opened
        method, headers, body = _record(seen, request)
        session = headers.get("mcp-session-id")
        if method == "initialize":
            if session is not None:
                return httpx.Response(400, json={"error": "initialize opens a session, it reuses none"})
            opened += 1
            return _initialized(body, body["params"]["protocolVersion"], f"s{opened}")
        if lost(method, session):
            return httpx.Response(404)
        if "id" not in body:
            return httpx.Response(202)
        if method == "tools/call":
            return _answer(body, {"content": [{"type": "text", "text": "done"}], "isError": False})
        return _answer(body, {"tools": TOOLS})

    return handle


async def test_a_lost_session_reinitializes_once() -> None:
    seen: Seen = []
    client = _client(_gateway(seen, lambda method, session: method == "tools/list" and session == "s1"))
    tools = await client.list_tools()
    assert [tool.name for tool in tools] == ["read_order"]
    assert _methods(seen).count("initialize") == 2
    assert [(m, h.get("mcp-session-id")) for m, h, _ in seen if m == "tools/list"] == [
        ("tools/list", "s1"),
        ("tools/list", "s2"),
    ]
    # The same request is sent again, under its JSON-RPC id, in the new session.
    first, again = (body for method, _, body in seen if method == "tools/list")
    assert first == again


async def test_a_second_404_is_an_error_not_a_loop() -> None:
    seen: Seen = []
    client = _client(_gateway(seen, lambda method, _: method == "tools/list"))
    with pytest.raises(McpError) as lost:
        await client.list_tools()
    assert lost.value.code == 404
    assert _methods(seen).count("initialize") == 2
    assert _methods(seen).count("tools/list") == 2


async def test_a_404_without_a_session_is_not_a_lost_session() -> None:
    """A server that issued no session has none to lose: its 404 (a wrong path, say) is an error at
    once, not a new `initialize`."""
    seen: Seen = []
    plain = _server(seen)

    def not_found(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content).get("method") == "tools/list":
            _record(seen, request)
            return httpx.Response(404)
        return plain(request)

    with pytest.raises(McpError) as missing:
        await _client(not_found).list_tools()
    assert missing.value.code == 404
    assert _methods(seen) == ["initialize", "notifications/initialized", "tools/list"]


async def test_a_reused_client_opens_its_next_session_without_the_former_id() -> None:
    """A client lists its tools, then calls one later (the runner's loop does): the second call
    opens a new session, and its `initialize` does not carry the id of the first."""
    seen: Seen = []
    client = _client(_gateway(seen, lambda _, session: session is None))
    assert [tool.name for tool in await client.list_tools()] == ["read_order"]
    assert (await client.call_tool("read_order", {}))["content"][0]["text"] == "done"
    assert [(m, h.get("mcp-session-id")) for m, h, _ in seen if m != "notifications/initialized"] == [
        ("initialize", None),
        ("tools/list", "s1"),
        ("initialize", None),
        ("tools/call", "s2"),
    ]


async def test_static_and_provided_headers_go_with_every_request() -> None:
    seen: Seen = []
    asked: list[bool] = []

    async def provider(refresh: bool) -> dict[str, str]:
        asked.append(refresh)
        return {"Authorization": "Bearer p1"}

    client = _client(_server(seen), headers={"X-Server": "github"}, headers_provider=provider)
    await client.list_tools()
    assert _methods(seen) == ["initialize", "notifications/initialized", "tools/list"]
    for method, headers, _ in seen:
        assert headers["x-server"] == "github", method
        assert headers["authorization"] == "Bearer p1", method
    assert asked == [False, False, False], "the provider is asked once per request, never to refresh"


class _TokenCache:
    """A provider in front of an OAuth token cache: the cached token, until a refresh renews it."""

    def __init__(self) -> None:
        self.token = "old"
        self.refreshes = 0

    async def __call__(self, refresh: bool) -> dict[str, str]:
        if refresh:
            self.refreshes += 1
            self.token = "new"
        return {"Authorization": f"Bearer {self.token}"}


async def test_a_401_refreshes_the_provider_once_then_fails() -> None:
    seen: Seen = []
    plain = _server(seen)

    def accepts_new(request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != "Bearer new":
            _record(seen, request)
            return httpx.Response(401)
        return plain(request)

    cache = _TokenCache()
    tools = await _client(accepts_new, headers_provider=cache).list_tools()
    assert [tool.name for tool in tools] == ["read_order"]
    assert cache.refreshes == 1
    assert [(m, h["authorization"]) for m, h, _ in seen] == [
        ("initialize", "Bearer old"),
        ("initialize", "Bearer new"),
        ("notifications/initialized", "Bearer new"),
        ("tools/list", "Bearer new"),
    ]

    refused_requests: Seen = []

    def refuses(request: httpx.Request) -> httpx.Response:
        _record(refused_requests, request)
        return httpx.Response(401)

    cache = _TokenCache()
    with pytest.raises(McpError, match="refused by the server") as refused:
        await _client(refuses, headers_provider=cache).list_tools()
    assert refused.value.code == 401
    assert cache.refreshes == 1
    assert _methods(refused_requests) == ["initialize", "initialize"]


async def test_token_and_provider_are_exclusive() -> None:
    async def provider(refresh: bool) -> dict[str, str]:
        return {}

    with pytest.raises(ValueError, match="not both"):
        McpClient(URL, token="t", headers_provider=provider)
