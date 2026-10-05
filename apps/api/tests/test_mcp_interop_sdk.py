"""Le client de référence du protocole parle à la porte MCP (ADR 0030).

La porte est écrite à la main : ses propres tests disent ce QU'ELLE croit du protocole. Celui-ci la
confronte au SDK `mcp` (dépendance de test seulement) servi par un vrai serveur HTTP : si le SDK
initialise, liste et appelle, un client réel — Claude Code, Cursor, VS Code — le fera aussi.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import pytest
import uvicorn
from httpx import AsyncClient


def _port_libre() -> int:
    with socket.socket() as prise:
        prise.bind(("127.0.0.1", 0))
        return int(prise.getsockname()[1])


async def test_le_client_de_reference_initialise_liste_et_appelle(
    app: Any, client: AsyncClient, project: dict[str, Any]
) -> None:
    pytest.importorskip("mcp")
    from mcp import ClientSession
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    cree = await client.post("/api/v1/me/tokens", json={"name": "sdk", "scopes": ["mcp:read"]})
    jeton = cree.json()["token"]

    port = _port_libre()
    serveur = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    )
    tache = asyncio.create_task(serveur.serve())
    try:
        for _ in range(100):
            if serveur.started:
                break
            await asyncio.sleep(0.05)
        http = create_mcp_http_client(headers={"Authorization": f"Bearer {jeton}"})
        async with (
            http,
            streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=http) as flux,
            ClientSession(flux[0], flux[1]) as session,
        ):
            await session.initialize()
            outils = await session.list_tools()
            noms = {outil.name for outil in outils.tools}
            assert {"list_projects", "get_work_item"} <= noms
            assert "create_work_item" not in noms, "un jeton de lecture ne voit pas l'écriture"
            resultat = await session.call_tool("list_projects", {})
            assert not resultat.is_error
            assert "varga:billing-api" in resultat.content[0].text
    finally:
        serveur.should_exit = True
        await tache
