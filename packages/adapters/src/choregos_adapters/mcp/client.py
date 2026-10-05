# SPDX-License-Identifier: Apache-2.0
"""Un client MCP, écrit à la main comme le serveur (ADR 0017) : la plateforme joint un serveur
MCP déclaré par l'organisation — `initialize`, `tools/list`, `tools/call` — en Streamable HTTP.

Ce qu'il fait, et pourquoi :
- `initialize` d'abord, puis `notifications/initialized` ; l'en-tête `Mcp-Session-Id` rendu par
  le serveur part ensuite avec chaque requête, `MCP-Protocol-Version` aussi ;
- une réponse arrive en JSON ou en flux SSE (`text/event-stream`) : on y lit le message qui
  porte l'identifiant de la requête, et rien d'autre ;
- `tools/list` suit `nextCursor` jusqu'au bout — un serveur pagine, et un outil de la page 2
  qu'on ne verrait pas ne serait jamais revu ;
- le jeton est CELUI DU CONNECTEUR, résolu par la plateforme : celui d'un run ne sort jamais.

Ce qu'il ne fait pas : les notifications du serveur, les requêtes du serveur vers le client
(échantillonnage, élicitation), la reprise d'un flux interrompu.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import httpx
from choregos_core.mcp import VERSIONS

#: Au-delà, un serveur qui pagine sans fin est une erreur, pas une liste.
PAGES_MAX = 50


class ErreurMcp(RuntimeError):  # noqa: N818 - une réponse du serveur, nommée telle quelle
    """Le serveur a répondu une erreur JSON-RPC, ou rien de lisible."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class OutilMcp:
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    #: `annotations.readOnlyHint` : un outil qui se déclare en lecture seule. Sans déclaration,
    #: il est tenu pour une écriture — le défaut prudent (ADR 0034).
    read_only: bool = False


def empreinte_du_schema(schema: dict[str, Any]) -> str:
    """L'empreinte canonique d'un schéma d'entrée : une dérive, même d'un caractère, la change."""
    canonique = json.dumps(schema or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonique.encode("utf-8")).hexdigest()


def _lire_sse(texte: str, identifiant: int) -> dict[str, Any] | None:
    """Le message JSON-RPC qui répond à `identifiant` dans un flux SSE ; les autres sont ignorés."""
    donnees: list[str] = []
    for ligne in [*texte.splitlines(), ""]:
        if ligne.startswith("data:"):
            donnees.append(ligne[5:].lstrip())
        elif ligne == "" and donnees:
            try:
                message = json.loads("\n".join(donnees))
            except ValueError:
                message = None
            donnees = []
            if isinstance(message, dict) and message.get("id") == identifiant:
                return message
    return None


class ClientMcp:
    def __init__(
        self,
        url: str,
        *,
        token: str = "",
        timeout_s: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = url
        self._token = token
        self._timeout = timeout_s
        self._transport = transport
        self._session: str | None = None
        self._version: str = VERSIONS[0]
        self._suivant = 0
        self.serveur: dict[str, Any] = {}

    def _entetes(self) -> dict[str, str]:
        entetes = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self._version,
        }
        if self._token:
            entetes["Authorization"] = f"Bearer {self._token}"
        if self._session:
            entetes["Mcp-Session-Id"] = self._session
        return entetes

    async def _envoyer(
        self, http: httpx.AsyncClient, methode: str, params: dict[str, Any] | None = None
    ) -> Any:
        self._suivant += 1
        identifiant = self._suivant
        corps: dict[str, Any] = {"jsonrpc": "2.0", "id": identifiant, "method": methode}
        if params is not None:
            corps["params"] = params
        reponse = await http.post(self.url, json=corps, headers=self._entetes())
        if reponse.status_code in {401, 403}:
            raise ErreurMcp(f"{methode} : refusé par le serveur ({reponse.status_code})", reponse.status_code)
        if reponse.status_code >= 400:
            raise ErreurMcp(f"{methode} : HTTP {reponse.status_code}", reponse.status_code)
        session = reponse.headers.get("mcp-session-id")
        if session:
            self._session = session
        if reponse.headers.get("content-type", "").startswith("text/event-stream"):
            message = _lire_sse(reponse.text, identifiant)
        else:
            try:
                message = reponse.json()
            except ValueError:
                message = None
        if not isinstance(message, dict):
            raise ErreurMcp(f"{methode} : réponse illisible")
        if "error" in message:
            erreur = message["error"] or {}
            raise ErreurMcp(f"{methode} : {erreur.get('message', 'erreur')}", erreur.get("code"))
        return message.get("result")

    async def _ouvrir(self, http: httpx.AsyncClient) -> None:
        resultat = await self._envoyer(
            http,
            "initialize",
            {
                "protocolVersion": VERSIONS[0],
                "capabilities": {},
                "clientInfo": {"name": "choregos", "version": "1"},
            },
        )
        self.serveur = dict(resultat or {})
        version = self.serveur.get("protocolVersion")
        if isinstance(version, str):
            self._version = version
        await http.post(
            self.url,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=self._entetes(),
        )

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, transport=self._transport)

    async def list_tools(self) -> list[OutilMcp]:
        async with self._http() as http:
            await self._ouvrir(http)
            outils: list[OutilMcp] = []
            curseur: str | None = None
            for _ in range(PAGES_MAX):
                resultat = await self._envoyer(http, "tools/list", {"cursor": curseur} if curseur else {})
                for brut in (resultat or {}).get("tools", []):
                    annotations = brut.get("annotations") or {}
                    outils.append(
                        OutilMcp(
                            name=str(brut["name"]),
                            description=str(brut.get("description") or ""),
                            input_schema=dict(brut.get("inputSchema") or {}),
                            read_only=annotations.get("readOnlyHint") is True,
                        )
                    )
                curseur = (resultat or {}).get("nextCursor")
                if not curseur:
                    return outils
            raise ErreurMcp(f"tools/list : plus de {PAGES_MAX} pages — le serveur pagine sans fin")

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async with self._http() as http:
            await self._ouvrir(http)
            resultat = await self._envoyer(http, "tools/call", {"name": name, "arguments": arguments})
            return dict(resultat or {})

    async def test(self) -> dict[str, Any]:
        outils = await self.list_tools()
        return {"ok": True, "server": self.serveur.get("serverInfo"), "tools": len(outils)}
