"""Limiteur de débit sur les routes SANS principal : connexion et webhooks.

`rate_limit_per_minute` existait dans les réglages sans qu'aucun code ne le lise (état des
lieux du 2026-09-24) : un réglage fantôme rassure autant qu'une garantie aveugle. Celui-ci
est réel, et modeste — une fenêtre glissante en mémoire, par adresse cliente, par réplique.
Ce qu'il arrête : un scan des routes d'authentification et un arrosage de webhooks depuis
une adresse. Ce qu'il n'arrête pas : une attaque distribuée, qui relève de l'entrée
(ingress, WAF). Les routes authentifiées ne passent pas ici : leur principal les borne.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

PREFIXES = ("/api/v1/auth/", "/api/v1/webhooks/")
FENETRE_S = 60.0


class Limiteur:
    def __init__(self, par_minute: int) -> None:
        self.par_minute = par_minute
        self._fenetres: dict[str, deque[float]] = {}

    def admet(self, cle: str, maintenant: float | None = None) -> tuple[bool, int]:
        """Rend (admis, secondes avant la prochaine place)."""
        if self.par_minute <= 0:
            return True, 0
        t = time.monotonic() if maintenant is None else maintenant
        fenetre = self._fenetres.setdefault(cle, deque())
        while fenetre and t - fenetre[0] >= FENETRE_S:
            fenetre.popleft()
        if len(fenetre) >= self.par_minute:
            return False, max(1, int(FENETRE_S - (t - fenetre[0])) + 1)
        fenetre.append(t)
        # Les clés muettes ne s'accumulent pas : une adresse vue une fois ne vit pas à jamais.
        if len(self._fenetres) > 10_000:
            for k in [k for k, f in self._fenetres.items() if not f or t - f[-1] >= FENETRE_S][:1_000]:
                self._fenetres.pop(k, None)
        return True, 0


def _adresse(request: Request) -> str:
    # Derrière l'ingress, l'adresse réelle est dans X-Forwarded-For (le premier élément).
    transmis = request.headers.get("x-forwarded-for", "")
    if transmis:
        return transmis.split(",", 1)[0].strip()
    return request.client.host if request.client else "inconnu"


def installer(app: FastAPI, par_minute: int) -> Limiteur:
    limiteur = Limiteur(par_minute)

    @app.middleware("http")
    async def _limiter(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if request.url.path.startswith(PREFIXES):
            admis, attente = limiteur.admet(_adresse(request))
            if not admis:
                return JSONResponse(
                    {
                        "type": "about:blank",
                        "title": "Trop de requêtes",
                        "status": 429,
                        "detail": f"plus de {par_minute} requêtes par minute depuis cette adresse",
                        "instance": request.url.path,
                    },
                    status_code=429,
                    headers={"Retry-After": str(attente)},
                )
        return await call_next(request)

    return limiteur
