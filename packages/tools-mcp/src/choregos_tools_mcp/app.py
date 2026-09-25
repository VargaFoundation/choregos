"""Sidecar HTTP du serveur MCP `choregos-tools` (endpoint `/mcp`)."""

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
        # Déjà injecté : le runner sert ces outils lui-même quand aucun sidecar ne le fait
        # (l'exécuteur `k8s_job` n'a qu'un conteneur). Il apporte alors SON client, déjà
        # authentifié par le jeton du run — en créer un second serait une seconde identité.
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
    """L'application du sidecar. `server` injecté : le runner la sert lui-même."""
    if server is not None:
        _state["server"] = server
    app = FastAPI(title="choregos-tools", version="1.0.0", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/mcp")
    async def mcp(request: Request) -> Response:
        """Un message JSON-RPC par requête ; une notification ne rend rien (204)."""
        server: McpServer = _state["server"]
        message = await request.json()
        if isinstance(message, list):  # lot JSON-RPC
            responses = [r for r in [await server.handle(m) for m in message] if r is not None]
            return JSONResponse(responses)
        response = await server.handle(message)
        if response is None:
            return Response(status_code=204)
        return JSONResponse(response)

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",  # le sidecar n'écoute que sur la boucle locale du pod
        port=int(os.environ.get("CHOREGOS_TOOLS_PORT", "7777")),
        log_level="warning",
    )


if __name__ == "__main__":
    main()
