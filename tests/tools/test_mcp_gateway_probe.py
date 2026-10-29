"""The MCP gateway probe: it lists tools through a gateway and never turns a failure into a skip."""

from __future__ import annotations

import socket

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


@pytest.mark.parametrize("token", ["sec ret\nvalue-SECRET", "SECRET\r\nX: y"])
def test_the_cli_never_prints_a_token_with_a_control_character(monkeypatch, capsys, token):
    monkeypatch.setenv("X", token)
    # A listening socket, so that the connection succeeds and h11 meets the header (the leak).
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        code = main([f"http://127.0.0.1:{port}/{{server}}/mcp", "--server", "s", "--token-env", "X"])
    captured = capsys.readouterr()
    assert code == 1
    assert "SECRET" not in captured.out + captured.err
    assert "sec ret" not in captured.out + captured.err


@pytest.mark.parametrize("token", ["sec ret\nvalue-SECRET", "SECRET\r\nX: y", "a\tb"])
async def test_the_probe_refuses_a_token_with_a_control_character(token):
    with pytest.raises(ValueError, match="control character") as raised:
        await probe(ENDPOINT, server="s", token=token, transport=gateway("t").transport())
    assert "SECRET" not in str(raised.value)


def test_the_cli_scrubs_the_token_and_its_repr_from_an_error(monkeypatch, capsys):
    token = "sec ret\nvalue-SECRET"

    async def leaky(*args, **kwargs):
        raise RuntimeError(f"Illegal header value b'Bearer {token}' / {token!r}")

    monkeypatch.setattr("tools.mcp_gateway_probe.probe", leaky)
    monkeypatch.setenv("X", token)
    code = main(["https://gw.test/{server}/mcp", "--server", "s", "--token-env", "X"])
    captured = capsys.readouterr()
    assert code == 1
    assert "SECRET" not in captured.out + captured.err
    assert "[redacted]" in captured.err
