"""The platform's MCP client (ADR 0034, S19-03) against a scriptable fake server."""

from __future__ import annotations

import base64

import pytest
from choregos_adapters.fakes.mcp import FakeMcpServer
from choregos_adapters.mcp import McpClient, McpError, schema_digest

TOOLS = [
    {
        "name": "lire_commande",
        "description": "reads",
        "inputSchema": {"type": "object"},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "commander_poste",
        "description": "orders a PC",
        "inputSchema": {"type": "object", "properties": {"model": {"type": "string"}}},
    },
    {"name": "suivi_commande", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}},
    {"name": "annuler_commande", "inputSchema": {"type": "object"}},
    {"name": "retour_materiel", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": False}},
]


@pytest.mark.parametrize("sse", [False, True], ids=["json", "sse"])
async def test_tools_list_follows_every_page_in_json_and_in_sse(sse: bool) -> None:
    server = FakeMcpServer(tools=TOOLS, page_size=2, sse=sse)
    client = McpClient("https://supplier.test/mcp", transport=server.transport())
    tools = await client.list_tools()
    assert [o.name for o in tools] == [o["name"] for o in TOOLS], "all five, over three pages"
    assert {o.name for o in tools if o.read_only} == {"lire_commande", "suivi_commande"}
    assert client.server["serverInfo"]["name"] == "fake-mcp"
    methods = [m for m, _, _ in server.received]
    assert methods[:2] == ["initialize", "notifications/initialized"]
    assert methods.count("tools/list") == 3


async def test_the_session_and_the_connector_token_travel_with_every_request() -> None:
    server = FakeMcpServer(tools=TOOLS[:1], token="connector-key")
    client = McpClient("https://supplier.test/mcp", token="connector-key", transport=server.transport())
    result = await client.call_tool("lire_commande", {"numero": "42"})
    assert result["content"][0]["text"] == "lire_commande done"
    assert server.calls == [("lire_commande", {"numero": "42"})]
    for method, headers, _ in server.received:
        assert headers["authorization"] == "Bearer connector-key", method
        if method != "initialize":
            assert headers["mcp-session-id"] == "session-1", method
        assert "cookie" not in headers


async def test_a_refusal_or_a_server_error_is_a_named_error() -> None:
    server = FakeMcpServer(tools=TOOLS[:1], token="expected")
    with pytest.raises(McpError, match="refused by the server"):
        await McpClient("https://f.test/mcp", token="other", transport=server.transport()).list_tools()
    server = FakeMcpServer(tools=TOOLS[:1])
    with pytest.raises(McpError, match="unknown tool"):
        await McpClient("https://f.test/mcp", transport=server.transport()).call_tool("absent", {})


def test_a_schema_digest_changes_with_the_smallest_difference() -> None:
    a = {"type": "object", "properties": {"model": {"type": "string"}}}
    b = {"properties": {"model": {"type": "string"}}, "type": "object"}
    assert schema_digest(a) == schema_digest(b), "the key order does not count"
    widened = {"type": "object", "properties": {"model": {"type": "string"}, "quantity": {"type": "integer"}}}
    assert schema_digest(a) != schema_digest(widened)


async def test_basic_sends_the_email_and_token_pair_and_never_bearer() -> None:
    """Atlassian's Rovo MCP Server refuses Bearer for an API token (S39-08a)."""
    server = FakeMcpServer(tools=TOOLS[:1])
    client = McpClient(
        "https://mcp.atlassian.com/v1/mcp",
        token="api-token",
        auth_scheme="basic",
        username="bot@example.com",
        transport=server.transport(),
    )
    await client.list_tools()
    expected = "Basic " + base64.b64encode(b"bot@example.com:api-token").decode()
    assert server.received
    for method, headers, _ in server.received:
        assert headers["authorization"] == expected, method
        assert "bearer" not in headers["authorization"].lower()


async def test_the_default_scheme_is_still_bearer() -> None:
    server = FakeMcpServer(tools=TOOLS[:1])
    await McpClient("https://f.test/mcp", token="t", transport=server.transport()).list_tools()
    assert all(headers["authorization"] == "Bearer t" for _, headers, _ in server.received)


def test_basic_without_a_username_or_an_unknown_scheme_is_refused() -> None:
    with pytest.raises(ValueError, match="username"):
        McpClient("https://f.test/mcp", token="t", auth_scheme="basic")
    with pytest.raises(ValueError, match="scheme"):
        McpClient("https://f.test/mcp", token="t", auth_scheme="digest")


async def test_the_connector_type_builds_a_basic_client(monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_adapters import build

    monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
    client = build(
        "mcp",
        "mcp",
        {
            "url": "https://mcp.atlassian.com/v1/mcp",
            "token": "t",
            "auth_scheme": "basic",
            "username": "a@b.c",
        },
    )
    assert (await client._request_headers())["Authorization"].startswith("Basic ")


async def test_a_custom_auth_header_carries_the_raw_key_and_no_authorization() -> None:
    """`auth_header: X-API-Key` (the catalogue's `header:<Name>` entries) sends the key as it is (S39-10c)."""
    server = FakeMcpServer(tools=TOOLS[:1])
    client = McpClient(
        "https://f.test/mcp", token="the-key", auth_header="X-API-Key", transport=server.transport()
    )
    await client.list_tools()
    assert server.received
    for method, headers, _ in server.received:
        assert headers["x-api-key"] == "the-key", method
        assert "authorization" not in headers, method


async def test_the_default_header_is_still_a_bearer() -> None:
    server = FakeMcpServer(tools=TOOLS[:1])
    await McpClient("https://f.test/mcp", token="t", transport=server.transport()).list_tools()
    assert all(headers["authorization"] == "Bearer t" for _, headers, _ in server.received)


async def test_the_platform_user_agent_travels_with_every_request() -> None:
    """ADR 0070: a server's operator sees who calls, and how to reach them."""
    server = FakeMcpServer(tools=TOOLS[:1])
    agent = "Choregos/1.2 (+https://ops.example.com)"
    client = McpClient("https://supplier.test/mcp", transport=server.transport(), user_agent=agent)
    await client.call_tool("lire_commande", {})
    assert server.received
    for method, headers, _ in server.received:
        assert headers["user-agent"] == agent, method


async def test_without_a_user_agent_the_client_keeps_the_httpx_one() -> None:
    server = FakeMcpServer(tools=TOOLS[:1])
    await McpClient("https://supplier.test/mcp", transport=server.transport()).call_tool("lire_commande", {})
    assert all("choregos" not in e["user-agent"].lower() for _, e, _ in server.received)
