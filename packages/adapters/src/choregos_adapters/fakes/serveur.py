# SPDX-License-Identifier: Apache-2.0
"""Les faux du scénario RH servis par UN processus (S20-07) : ce qu'il faut à un déploiement où l'API
et l'orchestrateur sont des processus distincts — le dev.

Un faux en mémoire tient son état dans le processus qui l'a construit : l'activité qui inscrit un
poste (l'orchestrateur) et le courtier qui relit son état (l'API) verraient deux parcs. Servis ici,
ils n'en voient qu'un. L'application est un appelable ASGI nu — le paquet des adaptateurs ne dépend
d'aucun cadriciel — que `uvicorn` sert : `python -m choregos_adapters.fakes.serveur`.

- `/{mdm,shipping,access_control,fournisseur,cd}/mcp` : chaque faux en serveur MCP (`serveur_mcp`),
  ce que joint un connecteur `demo` qui porte une `url`, ou un connecteur `mcp` — `cd` est
  l'environnement que promeut le train d'un projet de démonstration (S21-24) ;
- `/graph/v1.0/…` et `/login/{tenant}/oauth2/v2.0/token` : le faux Microsoft Graph, ce que joint un
  connecteur `entra` dont `graph_url` et `login_url` désignent ce service ;
- `/healthz`.

Un jeton (`CHOREGOS_DEMO_JETON`) protège les serveurs MCP et sert de secret d'application au faux
Graph ; vide, ils n'en demandent aucun — la politique réseau du déploiement reste alors la seule
porte, ce qui ne vaut que pour une démonstration.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from .cd_de_demo import FakeCdDeDemo
from .entra import FakeEntra
from .mcp import FakeMcpServer
from .rh import FAUX_PAR_FAMILLE, FakeFournisseur, FauxMetier, serveur_mcp

Recevoir = Callable[[], Awaitable[dict[str, Any]]]
Envoyer = Callable[[dict[str, Any]], Awaitable[None]]


class ApplicationDeDemo:
    def __init__(self, jeton: str = "") -> None:
        self.annuaire = FakeEntra(secret_attendu=jeton)
        self.faux: dict[str, FauxMetier] = {famille: classe() for famille, classe in FAUX_PAR_FAMILLE.items()}
        self.faux["fournisseur"] = FakeFournisseur()
        # L'environnement que promeut le train d'un projet de démonstration (S21-24).
        self.faux["cd"] = FakeCdDeDemo()
        self.serveurs: dict[str, FakeMcpServer] = {
            nom: serveur_mcp(faux, jeton=jeton) for nom, faux in self.faux.items()
        }

    def repondre(self, requete: httpx.Request) -> httpx.Response:
        chemin = requete.url.path
        if chemin == "/healthz":
            return httpx.Response(200, json={"ok": True})
        morceaux = chemin.strip("/").split("/")
        if len(morceaux) == 2 and morceaux[1] == "mcp" and morceaux[0] in self.serveurs:
            return self.serveurs[morceaux[0]].repondre(requete)
        if chemin.startswith("/login/"):
            return self.annuaire.repondre(requete)
        if chemin.startswith("/graph/"):
            sans_prefixe = requete.url.copy_with(path=chemin.removeprefix("/graph"))
            return self.annuaire.repondre(
                httpx.Request(requete.method, sans_prefixe, headers=requete.headers, content=requete.content)
            )
        return httpx.Response(404, json={"error": f"rien ici : {chemin}"})

    async def __call__(self, scope: dict[str, Any], receive: Recevoir, send: Envoyer) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return
        corps = b""
        while True:
            message = await receive()
            corps += message.get("body", b"")
            if not message.get("more_body"):
                break
        requete = httpx.Request(
            scope["method"],
            httpx.URL(path=scope["path"], query=scope.get("query_string", b"")).copy_with(
                scheme="http", host="demo"
            ),
            headers=[(cle.decode("latin-1"), valeur.decode("latin-1")) for cle, valeur in scope["headers"]],
            content=corps,
        )
        reponse = self.repondre(requete)
        contenu = reponse.content
        entetes = [(k.encode("latin-1"), v.encode("latin-1")) for k, v in reponse.headers.items()
                   if k.lower() not in {"content-length", "transfer-encoding"}]  # fmt: skip
        entetes.append((b"content-length", str(len(contenu)).encode()))
        await send({"type": "http.response.start", "status": reponse.status_code, "headers": entetes})
        await send({"type": "http.response.body", "body": contenu})


def application() -> ApplicationDeDemo:
    """La fabrique que `uvicorn --factory` appelle."""
    return ApplicationDeDemo(jeton=os.environ.get("CHOREGOS_DEMO_JETON", ""))


if __name__ == "__main__":  # pragma: no cover - le point d'entrée du conteneur
    import uvicorn

    uvicorn.run(application(), host="0.0.0.0", port=int(os.environ.get("CHOREGOS_DEMO_PORT", "8090")))
