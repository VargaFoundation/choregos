"""Un transport HTTP simulé qui se souvient de tout ce qui est parti sur le fil."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

Reponse = tuple[int, Any] | httpx.Response | Exception


@dataclass
class Fil:
    """`routes` : (méthode, chemin) → réponse, ou une liste de réponses consommées dans l'ordre.

    Le chemin est comparé sans la base : `/repos/x/y`. Une route absente répond 404 — comme
    un serveur qui ne connaît pas la ressource, et c'est souvent ce que le test veut.
    """

    routes: dict[tuple[str, str], Reponse | list[Reponse]] = field(default_factory=dict)
    requetes: list[httpx.Request] = field(default_factory=list)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._repondre))

    def _repondre(self, request: httpx.Request) -> httpx.Response:
        self.requetes.append(request)
        cle = (request.method, request.url.path)
        reponse = self.routes.get(cle)
        if isinstance(reponse, list):
            reponse = reponse.pop(0) if len(reponse) > 1 else reponse[0]
        if reponse is None:
            return httpx.Response(404, json={"message": "Not Found"})
        if isinstance(reponse, Exception):
            raise reponse
        if isinstance(reponse, httpx.Response):
            return reponse
        statut, corps = reponse
        if corps is None:
            return httpx.Response(statut)
        if isinstance(corps, str):
            return httpx.Response(statut, text=corps)
        return httpx.Response(statut, json=corps)

    def corps(self, index: int = -1) -> Any:
        return json.loads(self.requetes[index].content)

    def envoyees(self, methode: str | None = None) -> list[tuple[str, str]]:
        return [(r.method, r.url.path) for r in self.requetes if methode is None or r.method == methode]


def sans_attente(monkeypatch: Any, module: Any) -> list[float]:
    """Remplace `asyncio.sleep` du module par un enregistreur : les backoffs se lisent, ne durent pas."""
    attentes: list[float] = []

    async def _sleep(delai: float) -> None:
        attentes.append(delai)

    monkeypatch.setattr(module.asyncio, "sleep", _sleep)
    return attentes


def handler(fn: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(fn))
