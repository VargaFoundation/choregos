"""Live: a knowledge source of kind `mcp` against a real MCP server that publishes resources (ADR 0191).

    TRANIBOR_LIVE_MCP_RESOURCES_URL=http://localhost:3001/mcp \\
    uv run pytest tests/live/test_mcp_resources_live.py -m live -rs

Proved with the protocol's reference server, `@modelcontextprotocol/server-everything` 2.2.1
(`PORT=3001 npx @modelcontextprotocol/server-everything@2.2.1 streamableHttp`): seven Markdown
documents and one collection of five JSON items, all eight read.

Optional: `TRANIBOR_LIVE_MCP_RESOURCES_TOKEN` (a bearer), `TRANIBOR_LIVE_MCP_RESOURCES_PREFIX` (the
source's `uri_prefix`). An unset URL skips; a URL that is set means a server is expected: unreachable,
refusing, or listing nothing FAILS, never skips.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import pytest

pytestmark = pytest.mark.live


async def test_a_real_servers_resources_are_listed_to_the_end_and_read() -> None:
    url = os.environ.get("TRANIBOR_LIVE_MCP_RESOURCES_URL", "")
    if not url:
        pytest.skip("live test skipped — variables not set: TRANIBOR_LIVE_MCP_RESOURCES_URL")

    from tranibor_adapters.documents.base import ItemUnreadableError
    from tranibor_adapters.documents.mcp import McpResourceSource, checked_config
    from tranibor_adapters.mcp import McpClient
    from tranibor_core.egress import AllowedRule, EgressPolicy, use_policy

    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    # The sync runs in the workers: their family, the server's host alone.
    policy = EgressPolicy(
        org="live", rules=(AllowedRule(parts.hostname or "", (port,), ("workers",)),), mode="enforce"
    )
    config = {"uri_prefix": os.environ.get("TRANIBOR_LIVE_MCP_RESOURCES_PREFIX", "")}
    client = McpClient(url, token=os.environ.get("TRANIBOR_LIVE_MCP_RESOURCES_TOKEN") or None)
    source = McpResourceSource(client, **checked_config(config))

    with use_policy(policy, "workers", "live:knowledge-source"):
        changes = await source.changes(None)
        bodies = []
        for item in changes.items[:20]:
            try:
                bodies.append(await source.fetch(item))
            except ItemUnreadableError:
                continue
    assert changes.full is True
    assert changes.items, "the server lists no resource under the prefix"
    refs = [item.ref for item in changes.items]
    assert len(set(refs)) == len(refs), "one item per resource"
    assert len({item.name.lower() for item in changes.items}) == len(refs), "one file name per resource"
    assert bodies and all(isinstance(b, bytes) and b for b in bodies), "the first resources read"
