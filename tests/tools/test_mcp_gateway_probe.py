"""The MCP gateway probe: it lists tools through a gateway and never turns a failure into a skip."""

from __future__ import annotations

import httpx
import pytest
from choregos_adapters.fakes.mcp import FakeMcpServer
from choregos_adapters.fakes.mcp_gateway import FakeMcpGateway, GatewayUpstream
from choregos_adapters.mcp import CLIENT_VERSIONS, McpError

from tools.mcp_gateway_probe import main, probe

ENDPOINT = "https://gw.test/{server}/mcp"


def gateway(token: str) -> FakeMcpGateway:
    tool = {"name": "ping", "description": "ping", "inputSchema": {"type": "object"}}
    server = FakeMcpServer(outils=[tool])
    return FakeMcpGateway(auth="bearer", token=token, upstreams={"s": GatewayUpstream(server=server)})


async def test_the_probe_lists_tools_through_a_gateway():
    result = await probe(ENDPOINT, server="s", token="t", transport=gateway("t").transport())
    assert result["tools"] >= 1
    assert result["version"] in CLIENT_VERSIONS


async def test_the_probe_routes_by_header():
    fake = gateway("t")
    fake.routing = "header"
    transport = fake.transport()
    result = await probe("https://gw.test/mcp", server="s", token="t", routing="header", transport=transport)
    assert result["tools"] >= 1


async def test_an_unreachable_gateway_raises_and_does_not_skip():
    with pytest.raises((httpx.HTTPError, McpError)) as raised:
        await probe("http://127.0.0.1:9/{server}/mcp", server="s", token="t")
    assert not isinstance(raised.value, pytest.skip.Exception)


async def test_a_wrong_token_is_an_error():
    with pytest.raises(McpError) as raised:
        await probe(ENDPOINT, server="s", token="wrong", transport=gateway("right").transport())
    assert raised.value.code == 401


def test_the_cli_never_prints_the_token(monkeypatch, capsys):
    monkeypatch.setenv("X", "secret-value")
    code = main(["http://127.0.0.1:9/{server}/mcp", "--server", "s", "--token-env", "X"])
    captured = capsys.readouterr()
    assert code == 1
    assert "secret-value" not in captured.out + captured.err
