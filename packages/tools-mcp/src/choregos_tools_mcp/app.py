# SPDX-License-Identifier: Apache-2.0
"""The HTTP sidecar of the `choregos-tools` MCP server (endpoint `/mcp`)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

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
    client = InternalClient(
        os.environ.get("CHOREGOS_API_URL", "http://localhost:8000/api/v1/internal"),
        os.environ.get("CHOREGOS_RUN_ID", ""),
        os.environ.get("CHOREGOS_RUN_TOKEN", ""),
    )
    _state["client"] = client
    _state["server"] = McpServer(
        client, ToolContext(max_findings=int(os.environ.get("CHOREGOS_MAX_FINDINGS", "5")))
    )
    yield
    await client.aclose()


def create_app(server: McpServer | None = None) -> FastAPI:
    """The sidecar's application. With `server` injected, the runner serves it itself."""
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
