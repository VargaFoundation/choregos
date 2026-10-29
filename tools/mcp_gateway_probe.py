# SPDX-License-Identifier: Apache-2.0
"""Probes an organisation's MCP gateway with the platform's own MCP client (ADR 0105).

    CHOREGOS_GATEWAY_TOKEN=... uv run python tools/mcp_gateway_probe.py \\
        'https://gateway.example/{server}/mcp' --server github --token-env CHOREGOS_GATEWAY_TOKEN

It runs `initialize` and `tools/list` against one server behind the gateway and prints the agreed
protocol version, the server's identity and the number of tools, as JSON. The token is read from
the environment variable named by `--token-env` and is never printed.

`probe` does not catch anything: a gateway that is unreachable, refuses the token or speaks no
version of ours raises, so a caller (the nightly's live test) fails instead of skipping.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any
from urllib.parse import urlsplit

import httpx
from choregos_adapters.mcp import McpClient
from choregos_core.egress import AllowedRule, EgressPolicy, use_policy

_DEFAULT_PORTS = {"http": 80, "https": 443}


async def probe(
    endpoint: str,
    *,
    server: str,
    token: str,
    routing: str = "path",
    server_header: str = "X-MCP-Server",
    auth_header: str = "Authorization",
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Initialize against `server` behind the gateway and list its tools. Errors propagate."""
    if routing not in {"path", "header"}:
        raise ValueError(f"unknown routing `{routing}`: `path` or `header`")
    if not token.isprintable():
        # No value in the message: HTTP libraries echo an illegal header value, token included.
        raise ValueError("the token holds a control character")
    url = endpoint.replace("{server}", server) if routing == "path" else endpoint
    headers = {server_header: server} if routing == "header" else {}
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or _DEFAULT_PORTS.get(parts.scheme, 443)
    policy = EgressPolicy(org="live", rules=(AllowedRule(host, (port,), ("api",)),), mode="enforce")
    client = McpClient(url, headers=headers, token=token, auth_header=auth_header, transport=transport)
    with use_policy(policy, "api", "live:mcp-gateway"):
        tools = await client.list_tools()
    return {
        "version": client.negotiated_version,
        "server": client.server.get("serverInfo"),
        "tools": len(tools),
    }


def _scrub(text: str, token: str) -> str:
    """`text` without the token, nor the escaped form of it that an error message may carry."""
    for secret in (token, repr(token)[1:-1]):
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe an MCP gateway: initialize and tools/list.")
    parser.add_argument("endpoint", help="the gateway URL; `{server}` is replaced with --server")
    parser.add_argument("--server", required=True, help="the server behind the gateway")
    parser.add_argument("--token-env", required=True, help="the variable holding the gateway token")
    parser.add_argument("--routing", choices=["path", "header"], default="path")
    parser.add_argument("--server-header", default="X-MCP-Server")
    parser.add_argument("--auth-header", default="Authorization")
    args = parser.parse_args(argv)
    token = os.environ.get(args.token_env, "")
    try:
        result = asyncio.run(
            probe(
                args.endpoint,
                server=args.server,
                token=token,
                routing=args.routing,
                server_header=args.server_header,
                auth_header=args.auth_header,
            )
        )
    except Exception as error:
        # The type and the message of the error, with the token (and its repr form) scrubbed out.
        report = {"error": type(error).__name__, "detail": _scrub(str(error), token)}
        print(json.dumps(report), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
