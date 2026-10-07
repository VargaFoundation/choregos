# SPDX-License-Identifier: Apache-2.0
"""L'enveloppe du protocole MCP, écrite à la main et partagée (ADR 0017, ADR 0030).

Deux serveurs la parlent : le side-car `choregos-tools`, qui sert ses outils à l'agent d'un run, et
la porte `/mcp` de l'API, qui sert les clients d'un humain (Claude Code, Cursor, VS Code…). Ni l'un
ni l'autre n'embarque le SDK `mcp` : sept outils courts ne justifient ni des sessions ni un transport
de plus à auditer, et l'ADR 0011 a relevé des failles critiques dans `fastmcp`. Ce module ne fait
que ce que le protocole exige d'un serveur sans état — et le dit.

Ce qu'il ne fait PAS : les lots JSON-RPC (retirés du protocole en 2025-06-18), les flux SSE, les
sessions, les notifications du serveur vers le client.
"""

from __future__ import annotations

from typing import Any, Final

#: Les versions comprises, la plus récente d'abord. Un client qui en demande une autre reçoit la
#: plus récente : le protocole lui laisse le choix de se déconnecter.
VERSIONS: Final = ("2025-11-25", "2025-06-18", "2025-03-26")

#: Codes JSON-RPC 2.0 utilisés par MCP.
ERREUR_DE_SYNTAXE: Final = -32700
REQUETE_INVALIDE: Final = -32600
METHODE_INCONNUE: Final = -32601
PARAMETRES_INVALIDES: Final = -32602
ERREUR_INTERNE: Final = -32603


def negocier(demandee: object) -> str:
    """La version que le serveur répond à `initialize`."""
    return demandee if isinstance(demandee, str) and demandee in VERSIONS else VERSIONS[0]


def version_acceptable(entete: str | None) -> bool:
    """L'en-tête `MCP-Protocol-Version` d'une requête : absent, il vaut une version ancienne."""
    return entete is None or entete in VERSIONS


def ok(identifiant: Any, resultat: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": identifiant, "result": resultat}


def erreur(identifiant: Any, code: int, message: str, donnees: Any = None) -> dict[str, Any]:
    corps: dict[str, Any] = {"code": code, "message": message}
    if donnees is not None:
        corps["data"] = donnees
    return {"jsonrpc": "2.0", "id": identifiant, "error": corps}


def resultat_texte(texte: str, *, en_erreur: bool = False, structure: Any = None) -> dict[str, Any]:
    """Le résultat d'un outil : du texte pour le modèle, la structure pour le client qui la lit."""
    resultat: dict[str, Any] = {"content": [{"type": "text", "text": texte}], "isError": en_erreur}
    if structure is not None:
        resultat["structuredContent"] = structure
    return resultat


def est_notification(message: dict[str, Any]) -> bool:
    """Une notification n'a pas d'`id` : elle n'attend pas de réponse."""
    return "id" not in message


def defaut_du_message(message: object) -> str | None:
    """Pourquoi ce message n'est pas une requête JSON-RPC 2.0 acceptable, ou `None`."""
    if isinstance(message, list):
        return "JSON-RPC batches are not supported (MCP 2025-06-18)"
    if not isinstance(message, dict):
        return "un message JSON-RPC est un objet"
    if message.get("jsonrpc") != "2.0":
        return '`jsonrpc` doit valoir "2.0"'
    if not isinstance(message.get("method"), str):
        return "`method` manque"
    return None
