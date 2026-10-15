"""The sidecar's transport: a notification is accepted without a body, with 202.

MCP's Streamable HTTP transport wants 202 for a notification. The sidecar used to answer 204,
which clients tolerate although the protocol does not allow it; the API gateway (ADR 0030) and
the sidecar now share the same envelope (`choregos_core.mcp`).
"""

from __future__ import annotations

from typing import Any

from choregos_tools_mcp.app import create_app
from choregos_tools_mcp.server import McpServer
from httpx import ASGITransport, AsyncClient


async def test_a_notification_answers_202() -> None:
    application = create_app(server=McpServer(client=Any))  # type: ignore[arg-type]
    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://side") as http:
        response = await http.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert response.status_code == 202
    assert response.content == b""


async def test_the_requested_version_is_answered_when_understood() -> None:
    server = McpServer(client=Any)  # type: ignore[arg-type]
    response = await server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}
    )
    assert response is not None
    assert response["result"]["protocolVersion"] == "2025-11-25"
