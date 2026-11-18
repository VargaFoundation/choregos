# SPDX-License-Identifier: Apache-2.0
"""The HTTP sidecar of the `choregos-tools` MCP server (endpoint `/mcp`), and the tool bridge
(`/bridge/v1`, ADR 0122) when the run is offered tool scripts."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from . import bridge as tool_bridge
from .bridge import ApiJournal, Bridge, StageInputBridge
from .client import InternalClient
from .server import McpServer
from .tools import ToolContext

_state: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if "server" in _state:
        # Already injected: the runner serves these tools itself when no sidecar does (the
        # `k8s_job` executor has a single container). It then brings ITS client, already
        # authenticated with the run's token; creating a second one would be a second identity.
        yield
        return
    token = os.environ.get("CHOREGOS_RUN_TOKEN", "")
    client = InternalClient(
        os.environ.get("CHOREGOS_API_URL", "http://localhost:8000/api/v1/internal"),
        os.environ.get("CHOREGOS_RUN_ID", ""),
        token,
    )
    _state["client"] = client
    _state["server"] = McpServer(
        client, ToolContext(max_findings=int(os.environ.get("CHOREGOS_MAX_FINDINGS", "5")))
    )
    # The sidecar is not told its run's input: the bridge reads it from the API on first use.
    _state["bridge"] = StageInputBridge(client, ApiJournal(client), secrets=(token,))
    yield
    await client.aclose()


async def _sidecar_bridge() -> Bridge | None:
    source = _state.get("bridge")
    return None if source is None else await source()


def create_app(server: McpServer | None = None, bridge: Bridge | None = None) -> FastAPI:
    """The sidecar's application. With `server` injected, the runner serves it itself, and the tool
    bridge only when it hands one over: it knows its run's input. The sidecar (no `server`) always has
    the bridge's routes, and serves the bridge when the run's input, read from the API, offers it."""
    if server is not None:
        _state["server"] = server
    app = FastAPI(title="choregos-tools", version="1.0.0", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/mcp")
    async def mcp(request: Request) -> Response:
        """One JSON-RPC message per request; a notification is accepted without a body (202).

        The Streamable HTTP transport wants 202 for a notification; the sidecar used to answer
        204, which clients tolerate although the protocol does not allow it.
        """
        server: McpServer = _state["server"]
        message = await request.json()
        if isinstance(message, list):  # JSON-RPC batch
            responses = [r for r in [await server.handle(m) for m in message] if r is not None]
            return JSONResponse(responses)
        response = await server.handle(message)
        if response is None:
            return Response(status_code=202)
        return JSONResponse(response)

    if bridge is not None:

        async def offered() -> Bridge | None:
            return bridge

        tool_bridge.mount(app, offered)
    elif server is None:
        tool_bridge.mount(app, _sidecar_bridge)
    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",  # the sidecar listens on the pod's loopback only
        port=int(os.environ.get("CHOREGOS_TOOLS_PORT", "7777")),
        log_level="warning",
    )


if __name__ == "__main__":
    main()
