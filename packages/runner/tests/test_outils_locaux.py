"""Le runner sert les outils de la plateforme quand personne d'autre ne le fait.

Avec Tekton, un sidecar les sert. Avec l'exécuteur `k8s_job`, le pod d'agent n'a QU'UN
conteneur : personne n'écoutait sur l'adresse annoncée, et l'agent se retrouvait sans
aucun outil — tout en croyant en avoir, son `StageInput` lui promettant l'adresse. Le pire
des deux mondes : il tente, échoue en silence, et se rabat sur ce qu'il sait faire seul.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from choregos_runner.outils_locaux import arreter, demarrer_si_absent

pytestmark = pytest.mark.asyncio


class _ClientMuet:
    """Le serveur n'a besoin que d'un client : il ne s'en sert pas au démarrage."""

    run_id = "run-1"


async def test_une_adresse_distante_n_est_pas_notre_affaire() -> None:
    """Un serveur MCP hébergé ailleurs se joint, il ne se démarre pas."""
    assert await demarrer_si_absent("https://mcp.exemple/mcp", _ClientMuet()) is None  # type: ignore[arg-type]


async def test_un_sidecar_deja_en_place_garde_la_main() -> None:
    """On ne remplace pas ce qui marche : si quelqu'un écoute, on ne démarre rien."""
    serveur = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = serveur.sockets[0].getsockname()[1]
    try:
        poignee = await demarrer_si_absent(f"http://127.0.0.1:{port}/mcp", _ClientMuet())  # type: ignore[arg-type]
        assert poignee is None
    finally:
        serveur.close()
        await serveur.wait_closed()


async def test_sans_personne_a_l_ecoute_le_runner_sert_les_outils() -> None:
    """Et l'agent retrouve `report_finding`, `ask_human` et le catalogue."""
    import socket

    import httpx

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    poignee = await demarrer_si_absent(f"http://127.0.0.1:{port}/mcp", _ClientMuet())  # type: ignore[arg-type]
    assert poignee is not None, "personne n'écoutait : le runner devait prendre le relais"
    try:
        async with httpx.AsyncClient() as client:
            reponse = await client.post(
                f"http://127.0.0.1:{port}/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                timeout=5,
            )
        noms = [outil["name"] for outil in reponse.json()["result"]["tools"]]
        assert "report_finding" in noms, noms
    finally:
        await arreter(poignee)


async def test_arreter_accepte_l_absence(_: Any = None) -> None:
    """Rien à arrêter n'est pas une erreur : le chemin du sidecar passe par là."""
    await arreter(None)
