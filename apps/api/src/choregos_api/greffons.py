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
    """Pour les tests : aucun routeur de greffon."""
    _ROUTEURS.clear()
