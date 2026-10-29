# SPDX-License-Identifier: Apache-2.0
"""The `mcp-gateway` connector type (S46-05, ADR 0105): a server the organisation's MCP gateway
names. It has no secret and no host of its own, and no path builds a working client without the
configuration the platform injects, under fakes or not.

`FakeMcpServer`'s fields are French until S45 renames them: they are read in `_server()` and
`_received()` only.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import choregos_adapters
import pytest
from choregos_adapters import build, egress_hosts, spec_of, validate_config
from choregos_adapters.errors import AdapterError
from choregos_adapters.fakes.mcp import FakeMcpServer
from choregos_adapters.fakes.mcp_gateway import FakeMcpGateway, GatewayUpstream
from choregos_adapters.mcp import McpClient

TOOLS = [{"name": "read_file", "inputSchema": {"type": "object"}}]
OTHER_TOOLS = [{"name": "list_pages", "inputSchema": {"type": "object"}}]


def _server(tools: list[dict[str, Any]]) -> FakeMcpServer:
    return FakeMcpServer(outils=list(tools))


def _received(server: FakeMcpServer) -> list[tuple[str, dict[str, str], dict[str, Any]]]:
    return server.recues


def _injected(**more: Any) -> dict[str, Any]:
    return {
        "server": "x",
        "route": "gateway",
        "gateway_url": "https://gw.test/x/mcp",
        "gateway_headers": {"X-MCP-Server": "x"},
        **more,
    }


async def _names(client: McpClient) -> list[str]:
    return [tool.name for tool in await client.list_tools()]


def test_the_type_is_declared_without_secret_or_host() -> None:
    spec = spec_of("mcp", "mcp-gateway")
    assert spec.capabilities == ("mcp",)
    assert spec.secret_fields == ()
    assert spec.config_schema["required"] == ["server"]
    assert "default" not in spec.config_schema["properties"]["timeout_s"]
    assert egress_hosts("mcp", "mcp-gateway", {"server": "x", "url": "https://evil.test"}) == []


@pytest.mark.parametrize(
    "config",
    [
        {"server": "../x"},
        {"server": ""},
        {},
        {"server": "ok", "url": "https://x.test"},
        {"server": "ok", "token": "t"},
        {"server": "ok", "auth_header": "X-Key"},
    ],
)
def test_a_bad_server_name_or_a_direct_field_is_refused(config: dict[str, Any]) -> None:
    with pytest.raises(AdapterError):
        validate_config("mcp", "mcp-gateway", config)


def test_a_good_server_name_passes() -> None:
    validate_config("mcp", "mcp-gateway", {"server": "github.mcp"})


def test_the_real_factory_fails_closed_without_the_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
    with pytest.raises(AdapterError, match="only the platform supplies"):
        build("mcp", "mcp-gateway", {"server": "x"})
    with pytest.raises(AdapterError, match="only the platform supplies"):
        build("mcp", "mcp-gateway", {"server": "x", "route": "gateway"})
    with pytest.raises(AdapterError, match="name at the organisation's MCP gateway"):
        build("mcp", "mcp-gateway", _injected(server="../x"))


async def test_the_real_factory_uses_what_the_platform_injects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
    upstream = _server(TOOLS)
    gateway = FakeMcpGateway(upstreams={"x": GatewayUpstream(server=upstream)}, auth="none")

    async def provider(force: bool) -> Mapping[str, str]:
        return {"X-Caller": "alice"}

    client = build("mcp", "mcp-gateway", _injected(headers_provider=provider, transport=gateway.transport()))
    assert await _names(client) == ["read_file"]
    assert gateway.calls
    for call in gateway.calls:
        assert call.server == "x"
        assert call.headers["x-mcp-server"] == "x"
        assert call.headers["x-caller"] == "alice"


async def test_fakes_route_a_gateway_connector_to_the_fake_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHOREGOS_FAKES", "1")
    gateway = FakeMcpGateway(upstreams={"x": GatewayUpstream(server=_server(TOOLS))}, auth="none")
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP_GATEWAY", gateway)
    client = build("mcp", "mcp-gateway", _injected())
    assert await _names(client) == ["read_file"]
    assert gateway.calls, "the request went through the gateway"
    through_gateway = len(gateway.calls)
    direct = _server(OTHER_TOOLS)
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP", direct)
    assert await _names(build("mcp", "mcp", {"url": "https://s.test/mcp"})) == ["list_pages"]
    assert _received(direct), "a direct connector still reaches FAKE_MCP"
    assert len(gateway.calls) == through_gateway, "and never the gateway"


def test_fakes_refuse_a_gateway_connector_without_the_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHOREGOS_FAKES", "1")
    with pytest.raises(AdapterError, match="only the platform supplies"):
        build("mcp", "mcp-gateway", {"server": "x"})


async def test_the_fake_gateway_follows_the_current_fake_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHOREGOS_FAKES", "1")
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP_GATEWAY", None)
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP", _server(TOOLS))
    config = _injected(server="y", gateway_url="https://gw.test/y/mcp")
    assert await _names(build("mcp", "mcp-gateway", config)) == ["read_file"]
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP", _server(OTHER_TOOLS))
    assert await _names(build("mcp", "mcp-gateway", config)) == ["list_pages"]


async def test_a_lost_gateway_session_is_recovered_by_the_client() -> None:
    upstream = _server(TOOLS)
    gateway = FakeMcpGateway(
        upstreams={"x": GatewayUpstream(server=upstream)}, auth="none", forget_after_initialize=1
    )
    client = McpClient("https://gw.test/x/mcp", transport=gateway.transport())
    assert await _names(client) == ["read_file"]
    assert [m for m, _, _ in _received(upstream)].count("initialize") == 2
