"""Le serveur d'outils de la plateforme, servi par le runner lui-même.

POURQUOI
--------
`report_finding`, `ask_human`, `request_scope_change` et le catalogue d'outils sont exposés
à l'agent par un serveur MCP en **localhost**. Ce serveur vivait dans un *sidecar*, ce qui
n'existe qu'avec Tekton : avec l'exécuteur `k8s_job`, le pod d'agent n'a **qu'un conteneur**.
Personne n'écoutait sur le port annoncé, et l'agent se retrouvait sans aucun outil — tout en
croyant en avoir, puisque son `StageInput` lui promettait l'adresse.

C'est le pire des deux mondes : l'agent tente, échoue en silence, et se rabat sur ce qu'il
sait faire seul. Sur le banc du 2026-09-24, cela ressemblait à des agents « autonomes » ;
c'étaient des agents privés de leurs outils.

Le runner le démarre donc lui-même quand l'adresse annoncée est locale et que rien ne répond.
Un sidecar, s'il existe, garde la main : on ne remplace pas ce qui marche.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any
from urllib.parse import urlparse

from .client import InternalClient


def _est_local(url: str) -> tuple[str, int] | None:
    """L'adresse annoncée est-elle sur la boucle locale ? Rend (hôte, port) si oui."""
    decoupe = urlparse(url)
    if decoupe.hostname not in {"localhost", "127.0.0.1"}:
        return None
    return (decoupe.hostname, decoupe.port or 80)


async def _repond_deja(hote: str, port: int) -> bool:
    """Quelqu'un écoute-t-il déjà ? Un sidecar garde la main."""
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(hote, port), timeout=1.0)
    except (OSError, TimeoutError):
        return False
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return True


async def demarrer_si_absent(url: str, client: InternalClient, *, max_findings: int = 5) -> Any:
    """Démarre le serveur d'outils sur `url` si rien n'y répond. Rend de quoi l'arrêter.

    Rend `None` quand il n'y a rien à faire — adresse distante, ou sidecar déjà en place.
    Une panne au démarrage n'est pas fatale : l'agent perdra ses outils, ce qui est déjà le
    cas aujourd'hui, mais l'étape continue et le journal le dit.
    """
    adresse = _est_local(url)
    if adresse is None:
        return None
    hote, port = adresse
    if await _repond_deja(hote, port):
        return None

    import uvicorn
    from choregos_tools_mcp.app import create_app
    from choregos_tools_mcp.server import McpServer
    from choregos_tools_mcp.tools import ToolContext

    # Le serveur partage le client du runner : même jeton de run, même portée. Il n'a
    # besoin de rien d'autre — et surtout d'aucun identifiant de fournisseur.
    app = create_app(McpServer(client, ToolContext(max_findings=max_findings)))
    config = uvicorn.Config(app, host=hote, port=port, log_level="warning")
    serveur = uvicorn.Server(config)
    tache = asyncio.create_task(serveur.serve())
    for _ in range(50):  # au plus 5 s : le pod démarre vite, l'agent attend
        if await _repond_deja(hote, port):
            return (serveur, tache)
        await asyncio.sleep(0.1)
    serveur.should_exit = True
    return None


async def arreter(poignee: Any) -> None:
    if poignee is None:
        return
    serveur, tache = poignee
    serveur.should_exit = True
    with contextlib.suppress(Exception):
        await asyncio.wait_for(tache, timeout=5)
