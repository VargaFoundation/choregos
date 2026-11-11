"""Helpers shared by the assistant genericity tests."""

from __future__ import annotations

from httpx import AsyncClient

from ..kits.assistant_install import (
    ORG,
    offline,  # noqa: F401
)
from ..test_agent_sessions import Upstream, _ledger, upstream  # noqa: F401

AUDIENCE = "investors"


TOOLS = [
    {"name": n, "description": n, "annotations": {"readOnlyHint": True}, "inputSchema": {"type": "object"}}
    for n in ("search_news", "get_article", "get_quote", "get_history", "lookup_instrument")
]


async def _served(client: AsyncClient, name: str) -> None:
    body = {"name": name, "type": "mcp", "config": {"url": f"https://{name}.test/mcp"},
            "secret_refs": {"token": "env:CONNECTOR_TEST_FEED_KEY"}}  # fmt: skip
    assert (await client.post(f"{ORG}/connectors", json=body)).status_code == 201
    assert (await client.post(f"{ORG}/connectors/{name}/discover")).status_code == 200
    for tool in TOOLS:
        opened = await client.patch(
            f"{ORG}/connectors/{name}/operations/{tool['name']}", json={"policy": "allowed"}
        )
        assert opened.status_code == 200, opened.text
