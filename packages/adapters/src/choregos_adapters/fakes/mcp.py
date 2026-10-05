# SPDX-License-Identifier: Apache-2.0
"""Un faux serveur MCP, scriptable (ADR 0005) : une application ASGI que le client joint par un
transport en mémoire. Il sert ses outils par pages, répond en JSON ou en SSE, exige une session
après `initialize`, et note chaque requête — de quoi vérifier ce qui sort de la plateforme."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class FakeMcpServer:
    outils: list[dict[str, Any]] = field(default_factory=list)
    #: le jeton attendu ; vide : aucun
    jeton: str = ""
    par_page: int = 2
    sse: bool = False
    #: (méthode, en-têtes, corps) de chaque requête reçue
    recues: list[tuple[str, dict[str, str], dict[str, Any]]] = field(default_factory=list)
    appels: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    session: str = "session-1"
    #: le texte que rend un outil ; par défaut « <outil> fait »
    reponses: dict[str, str] = field(default_factory=dict)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._repondre)

    def _repondre(self, requete: httpx.Request) -> httpx.Response:
        corps = json.loads(requete.content or b"{}")
        entetes = {k.lower(): v for k, v in requete.headers.items()}
        methode = str(corps.get("method", ""))
        self.recues.append((methode, entetes, corps))
        if self.jeton and entetes.get("authorization") != f"Bearer {self.jeton}":
            return httpx.Response(401, json={"error": "unauthorized"})
        if "id" not in corps:
            return httpx.Response(202)
        if methode != "initialize" and entetes.get("mcp-session-id") != self.session:
            return httpx.Response(400, json={"error": "session manquante"})
        resultat = self._resultat(methode, corps.get("params") or {})
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": corps["id"]}
        if isinstance(resultat, tuple):
            message["error"] = {"code": resultat[0], "message": resultat[1]}
        else:
            message["result"] = resultat
        entetes_reponse = {"Mcp-Session-Id": self.session} if methode == "initialize" else {}
        if self.sse:
            texte = 'event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n'
            texte += f"event: message\ndata: {json.dumps(message)}\n\n"
            return httpx.Response(
                200, text=texte, headers={"Content-Type": "text/event-stream", **entetes_reponse}
            )
        return httpx.Response(200, json=message, headers=entetes_reponse)

    def _resultat(self, methode: str, params: dict[str, Any]) -> dict[str, Any] | tuple[int, str]:
        if methode == "initialize":
            return {
                "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-mcp", "version": "1"},
            }
        if methode == "tools/list":
            debut = int(params.get("cursor") or 0)
            page = self.outils[debut : debut + self.par_page]
            suite = debut + self.par_page
            return {"tools": page, **({"nextCursor": str(suite)} if suite < len(self.outils) else {})}
        if methode == "tools/call":
            nom = str(params.get("name"))
            if not any(o["name"] == nom for o in self.outils):
                return (-32602, f"outil inconnu : {nom}")
            arguments = dict(params.get("arguments") or {})
            self.appels.append((nom, arguments))
            texte = self.reponses.get(nom, f"{nom} fait")
            return {"content": [{"type": "text", "text": texte}], "isError": False}
        return (-32601, f"méthode inconnue : {methode}")
