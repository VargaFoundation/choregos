# SPDX-License-Identifier: Apache-2.0
"""Les routes qu'un greffon ajoute à l'API — sans forker `main.py`.

Un greffon (`choregos.plugins`, voir `choregos_adapters.charger_les_greffons`) savait déjà
enregistrer un connecteur, une garantie, déclarer une édition ou un mappeur de groupes. Il ne
pouvait pas **servir** quoi que ce soit : `create_app()` n'incluait que ses propres routeurs. Toute
fonctionnalité qui a besoin d'une route — le cycle de vie d'une organisation, une intégration
maison — demandait donc un patch du cœur.

Le greffon appelle `declarer_un_routeur(routeur)` depuis son `brancher()` ; `create_app()`
l'inclut sous `/api/v1`, **après** ceux du cœur. Les dépendances d'authentification et de droits
restent celles que le routeur déclare lui-même, comme pour n'importe quel routeur du cœur.

**Une route du greffon qui recouvre une route du cœur (même chemin, même méthode) arrête le
démarrage.** Starlette sert la première route qui correspond : le greffon serait ignoré sans un
mot — ou, si l'ordre changeait un jour, il remplacerait en silence une route du cœur, y compris
une route d'authentification. Un remplacement n'est pas un ajout ; il se demande par une couture
nommée, comme le mappeur de groupes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter
from fastapi.routing import APIRoute

_ROUTEURS: list[APIRouter] = []


def declarer_un_routeur(routeur: APIRouter) -> None:
    """Appelée par un greffon à son chargement. Le cœur ne l'appelle jamais."""
    if not isinstance(routeur, APIRouter):
        raise TypeError(f"un routeur FastAPI est attendu, pas {type(routeur).__name__}")
    if routeur not in _ROUTEURS:
        _ROUTEURS.append(routeur)


def routeurs_declares() -> tuple[APIRouter, ...]:
    return tuple(_ROUTEURS)


def signatures(routeur: APIRouter, prefixe: str = "") -> set[tuple[str, str]]:
    """{(méthode, chemin)} servis par un routeur, préfixe compris.

    Un routeur IMBRIQUÉ (`include_router` dans le routeur) est refusé plutôt que sauté : FastAPI
    ne l'expose que par un objet privé, et une route qu'on ne voit pas est une route dont on ne
    peut pas garantir qu'elle ne recouvre rien.
    """
    servies: set[tuple[str, str]] = set()
    for route in routeur.routes:
        if not isinstance(route, APIRoute):
            raise TypeError(
                f"route {type(route).__name__} dans un routeur de greffon : seules les routes "
                "directes (`@routeur.get`, `@routeur.post`…) sont prises en charge, pas un routeur imbriqué."
            )
        for methode in route.methods or ():
            servies.add((methode, prefixe + route.path))
    return servies


def reinitialiser() -> None:
    """Pour les tests : aucun routeur de greffon, aucun contrôle de geste."""
    _ROUTEURS.clear()
    reinitialiser_les_controles()


# ───────────────────────────── contrôle des gestes humains ─────────────────────────────
#
# Certains gestes humains portent une règle d'exploitation que le cœur ne connaît pas : un plafond
# de projets par organisation, une authentification FRAÎCHE pour approuver une mise en production.
# Un greffon déclare un contrôle, joué par la route AVANT qu'elle agisse — après les droits du
# cœur, qui restent les premiers juges : un contrôle ne peut que refuser davantage.

#: Les gestes contrôlables, et ce que la route passe dans `cible`.
GESTES = {
    "project.create": "slug du projet à créer",
    "workitem.decision": "ticket, transition, nature de la demande et de la décision",
    "release.approve": "release, environnement",
}


@dataclass(frozen=True)
class DemandeDeGeste:
    geste: str
    org: str
    principal: Any
    cible: dict[str, Any] = field(default_factory=dict)


class GesteRefuse(Exception):  # noqa: N818 - une décision, pas une erreur
    """`nature` choisit la réponse : `interdit` (403), `conflit` (409), `reauth` (401 — refaire
    `GET /api/v1/auth/login?reauth=1`, puis le geste)."""

    def __init__(self, raison: str, nature: str = "interdit") -> None:
        if nature not in {"interdit", "conflit", "reauth"}:
            raise ValueError(f"nature inconnue : {nature!r}")
        super().__init__(raison)
        self.nature = nature


_CONTROLES: dict[str, dict[str, Any]] = {geste: {} for geste in GESTES}


def declarer_un_controle_de_geste(geste: str, nom: str, fonction: Any) -> None:
    """`fonction(session_db, demande)`, synchrone ou asynchrone ; lève `GesteRefuse` pour refuser.
    Toute autre exception remonte : un contrôle en panne n'est pas une autorisation."""
    if geste not in GESTES:
        raise ValueError(f"geste inconnu : {geste!r} (connus : {sorted(GESTES)})")
    _CONTROLES[geste][nom] = fonction


async def controler(session: Any, demande: DemandeDeGeste) -> None:
    """Appelée par les routes du cœur. Traduit le premier refus en réponse HTTP, préfixée du nom."""
    import inspect

    from .errors import conflict, forbidden, unauthorized

    for nom, fonction in _CONTROLES[demande.geste].items():
        try:
            resultat = fonction(session, demande)
            if inspect.isawaitable(resultat):
                await resultat
        except GesteRefuse as refus:
            raison = f"{nom} : {refus}"
            if refus.nature == "conflit":
                raise conflict(raison) from refus
            if refus.nature == "reauth":
                raise unauthorized(
                    f"{raison} — se ré-authentifier (GET /api/v1/auth/login?reauth=1)"
                ) from refus
            raise forbidden(raison) from refus


def reinitialiser_les_controles() -> None:
    for controles in _CONTROLES.values():
        controles.clear()
