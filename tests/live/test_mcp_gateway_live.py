"""Live: the platform's MCP client against a real MCP gateway (ADR 0105).

    CHOREGOS_LIVE_MCP_GATEWAY_URL='https://gateway.example/{server}/mcp' \\
    CHOREGOS_LIVE_MCP_GATEWAY_SERVER=github CHOREGOS_LIVE_MCP_GATEWAY_TOKEN=... \\
    uv run pytest tests/live/test_mcp_gateway_live.py -m live -rs

Optional: `CHOREGOS_LIVE_MCP_GATEWAY_ROUTING` (`path` or `header`), `_SERVER_HEADER` and
`_AUTH_HEADER`. An unset URL skips (no gateway to prove). A URL that is set means a gateway is
expected: a missing server or token, an unreachable gateway or a refused token FAILS, never skips.
The gateway is deliberately not in `CHOREGOS_LIVE_REQUIRED`.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

pytestmark = pytest.mark.live

# `tools` is not a package and tests/live and tests/e2e are outside `make test`: put the repository
# root on sys.path here, and import the probe inside the test so that collection never fails.
ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def test_a_real_gateway_lists_a_servers_tools():
    url = os.environ.get("CHOREGOS_LIVE_MCP_GATEWAY_URL", "")
    if not url:
        pytest.skip("live test skipped — variables not set: CHOREGOS_LIVE_MCP_GATEWAY_URL")
    server = os.environ.get("CHOREGOS_LIVE_MCP_GATEWAY_SERVER", "")
    token = os.environ.get("CHOREGOS_LIVE_MCP_GATEWAY_TOKEN", "")
    assert server, "CHOREGOS_LIVE_MCP_GATEWAY_URL is set but CHOREGOS_LIVE_MCP_GATEWAY_SERVER is not"
    assert token, "CHOREGOS_LIVE_MCP_GATEWAY_URL is set but CHOREGOS_LIVE_MCP_GATEWAY_TOKEN is not"

    from choregos_adapters.mcp import CLIENT_VERSIONS

    from tools.mcp_gateway_probe import probe

    result = await probe(
        url,
        server=server,
        token=token,
        routing=os.environ.get("CHOREGOS_LIVE_MCP_GATEWAY_ROUTING") or "path",
        server_header=os.environ.get("CHOREGOS_LIVE_MCP_GATEWAY_SERVER_HEADER") or "X-MCP-Server",
        auth_header=os.environ.get("CHOREGOS_LIVE_MCP_GATEWAY_AUTH_HEADER") or "Authorization",
    )
    assert result["version"] in CLIENT_VERSIONS
    assert result["tools"] >= 1
