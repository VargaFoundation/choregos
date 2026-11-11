"""Helpers shared by the assistants-over-MCP tests."""

from __future__ import annotations

from typing import Any

import httpx
from httpx import AsyncClient

from ..kits.assistant_door import ORG, connector, idp, idp_token, install_plugin, offline  # noqa: F401
from ..test_agent_sessions import Upstream, upstream  # noqa: F401


async def _rpc(
    client: AsyncClient, token: str, method: str, params: dict[str, Any] | None = None, path: str = "/mcp"
) -> httpx.Response:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    return await client.post(path, json=body, headers={"Authorization": f"Bearer {token}"})


async def _call(
    client: AsyncClient, token: str, tool: str, arguments: dict[str, Any], path: str = "/mcp"
) -> dict[str, Any]:
    response = await _rpc(client, token, "tools/call", {"name": tool, "arguments": arguments}, path)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()["result"]
    assert not result["isError"], result
    return dict(result["structuredContent"])


async def _names(client: AsyncClient, token: str, path: str = "/mcp") -> set[str]:
    response = await _rpc(client, token, "tools/list", path=path)
    assert response.status_code == 200, response.text
    return {t["name"] for t in response.json()["result"]["tools"]}
