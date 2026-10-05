"""Le transport du side-car : une notification est acceptée sans corps, en 202.

Le transport Streamable HTTP de MCP veut 202 pour une notification. Le side-car rendait 204, que
les clients tolèrent sans que le protocole le permette ; la porte de l'API (ADR 0030) et lui
partagent désormais la même enveloppe (`choregos_core.mcp`).
"""

from __future__ import annotations

from typing import Any

from choregos_tools_mcp.app import create_app
from choregos_tools_mcp.server import McpServer
from httpx import ASGITransport, AsyncClient


async def test_une_notification_rend_202() -> None:
    application = create_app(server=McpServer(client=Any))  # type: ignore[arg-type]
    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://cote") as http:
        reponse = await http.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert reponse.status_code == 202
    assert reponse.content == b""


async def test_la_version_demandee_est_rendue_si_elle_est_comprise() -> None:
    serveur = McpServer(client=Any)  # type: ignore[arg-type]
    reponse = await serveur.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}
    )
    assert reponse is not None
    assert reponse["result"]["protocolVersion"] == "2025-11-25"
