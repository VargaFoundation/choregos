# SPDX-License-Identifier: Apache-2.0
"""The platform's tools server, served by the runner itself.

POURQUOI
--------
`report_finding`, `ask_human`, `request_scope_change` and the tool catalogue are exposed
to the agent by an MCP server on **localhost**. This server used to live in a *sidecar*, which
exists only with Tekton: with the `k8s_job` executor, the agent pod has **only one container**.
Nobody was listening on the announced port, and the agent ended up with no tool at all, while
believing it had some, since its `StageInput` promised it the address.

It is the worst of both worlds: the agent tries, fails silently, and falls back on what it
can do alone. On the 2026-09-24 bench, this looked like "autonomous" agents; they were
agents deprived of their tools.

The runner therefore starts it itself when the announced address is local and nothing answers.
A sidecar, if there is one, keeps control: we do not replace what works.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from .client import InternalClient

if TYPE_CHECKING:
    from choregos_tools_mcp.bridge import Bridge


def _local_address(url: str) -> tuple[str, int] | None:
    """Is the announced address on the loopback? Returns (host, port) if so."""
    parts = urlparse(url)
    if parts.hostname not in {"localhost", "127.0.0.1"}:
        return None
    return (parts.hostname, parts.port or 80)


async def _already_answers(host: str, port: int) -> bool:
    """Is someone already listening? A sidecar keeps control."""
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=1.0)
    except (OSError, TimeoutError):
        return False
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return True


async def start_if_absent(
    url: str, client: InternalClient, *, max_findings: int = 5, bridge: Bridge | None = None
) -> Any:
    """Start the tools server on `url` if nothing answers there. Returns what is needed to stop it.

    Returns `None` when there is nothing to do: remote address, or sidecar already in place.
    A failure at startup is not fatal: the agent will lose its tools, which is already the
    case today, but the step goes on and the journal says so. `bridge`, when the run is offered
    tool scripts (ADR 0122), is served by the same server, on the same port: a sidecar in place
    serves its own.
    """
    address = _local_address(url)
    if address is None:
        return None
    host, port = address
    if await _already_answers(host, port):
        return None

    import uvicorn
    from choregos_tools_mcp.app import create_app
    from choregos_tools_mcp.server import McpServer
    from choregos_tools_mcp.tools import ToolContext

    # The server shares the runner's client: same run token, same scope. It needs
    # nothing else, and above all no provider credential.
    app = create_app(McpServer(client, ToolContext(max_findings=max_findings)), bridge=bridge)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(50):  # at most 5 s: the pod starts fast, the agent waits
        if await _already_answers(host, port):
            return (server, task)
        await asyncio.sleep(0.1)
    server.should_exit = True
    return None


async def stop(handle: Any) -> None:
    if handle is None:
        return
    server, task = handle
    server.should_exit = True
    with contextlib.suppress(Exception):
        await asyncio.wait_for(task, timeout=5)
