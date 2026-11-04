"""The protocol's reference client talks to the MCP door (ADR 0030).

The door is written by hand: its own tests say what IT believes of the protocol. This one
confronts it with the `mcp` SDK (a test-only dependency) served by a real HTTP server: if the SDK
initialises, lists and calls, a real client (Claude Code, Cursor, VS Code) will too.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import pytest
import uvicorn
from httpx import AsyncClient


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def test_the_reference_client_initialises_lists_and_calls(
    app: Any, client: AsyncClient, project: dict[str, Any]
) -> None:
    pytest.importorskip("mcp")
    from mcp import ClientSession
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    created = await client.post("/api/v1/me/tokens", json={"name": "sdk", "scopes": ["mcp:read"]})
    token = created.json()["token"]

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    )
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
        async with (
            http,
            streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=http) as stream,
            ClientSession(stream[0], stream[1]) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            assert {"list_projects", "get_work_item"} <= names
            assert "create_work_item" not in names, "a read token does not see writing"
            result = await session.call_tool("list_projects", {})
            assert not result.is_error
            assert "varga:billing-api" in result.content[0].text
    finally:
        server.should_exit = True
        await task
