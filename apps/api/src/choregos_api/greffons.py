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
    """Pour les tests : aucun routeur de greffon, aucun contrôle de geste, aucun outil."""
    _ROUTEURS.clear()
    _FOURNISSEURS_D_OUTILS.clear()
    _FOURNISSEURS_HUMAINS.clear()
    reinitialiser_les_controles()


# ───────────────────────────── outils des runs ─────────────────────────────
#
# Le catalogue d'outils (`catalogue.py`) décrit des API tierces, déclarées par le déploiement dans
# un fichier. Un greffon peut avoir les siens, qui dépendent du PROJET du run et non du
# déploiement : les outils générés depuis l'ontologie d'un projet, par exemple. Ils passent par le
# même chemin que ceux du catalogue — `GET /internal/runs/{id}/tools` les annonce au serveur MCP
# `choregos-tools` de l'agent, `POST /internal/runs/{id}/tools/{nom}` les appelle sous le même
# plafond d'appels par run, avec la même ligne au registre des coûts et le même événement. Le
# jeton du run reste l'unique authentification : le fournisseur reçoit le run et son projet, déjà
# vérifiés, jamais le jeton.


@dataclass(frozen=True)
class FournisseurDOutils:
    """`lister(session, run, projet)` rend des outils au format MCP (`name`, `description`,
    `inputSchema`) ; `appeler(session, run, projet, nom, arguments)` rend `(code HTTP, corps)`.
    Un code ≥ 400 est une réponse que l'agent lit, pas une panne."""

    lister: Any
    appeler: Any


_FOURNISSEURS_D_OUTILS: dict[str, FournisseurDOutils] = {}


def declarer_un_fournisseur_d_outils(nom: str, lister: Any, appeler: Any) -> None:
    """Appelée par un greffon à son chargement. Deux greffons ne déclarent pas le même nom."""
    if nom in _FOURNISSEURS_D_OUTILS and _FOURNISSEURS_D_OUTILS[nom] != FournisseurDOutils(lister, appeler):
        raise ValueError(f"fournisseur d'outils « {nom} » déjà déclaré")
    _FOURNISSEURS_D_OUTILS[nom] = FournisseurDOutils(lister, appeler)


def fournisseurs_d_outils() -> dict[str, FournisseurDOutils]:
    return dict(_FOURNISSEURS_D_OUTILS)


# ───────────────────────────── outils servis aux humains, par la porte MCP ─────────────────────────────


@dataclass(frozen=True)
class FournisseurHumain:
    """Les outils qu'un greffon sert à la porte MCP des clients externes (ADR 0030), avec les droits
    de l'HUMAIN qui a frappé le jeton — jamais ceux d'un agent de la plateforme.

    - `lister(session, principal, projet, org)` rend des outils au format MCP (`name`,
      `description`, `inputSchema`), plus `ecriture: bool` : un outil d'écriture n'est annoncé qu'à
      un jeton `mcp:write` ;
    - `appeler(session, principal, projet, org, nom, arguments)` rend `(code HTTP, corps)` ; un code
      ≥ 400 est un refus que le modèle lit ;
    - `en_attente(session, principal, projet, org)`, facultatif, rend ce qui attend une décision
      humaine : `[{key, title, kind, question, requested_at, can_decide, decision_path}]`, où
      `decision_path` est le chemin de la console où la décision se prend.

    Un outil d'un greffon ne prend jamais le nom d'un outil du cœur : la porte l'écarterait.
    """

    lister: Any
    appeler: Any
    en_attente: Any = None


_FOURNISSEURS_HUMAINS: dict[str, FournisseurHumain] = {}


def declarer_des_outils_pour_les_humains(nom: str, lister: Any, appeler: Any, en_attente: Any = None) -> None:
    """Appelée par un greffon à son chargement. Deux greffons ne déclarent pas le même nom."""
    fournisseur = FournisseurHumain(lister, appeler, en_attente)
    if nom in _FOURNISSEURS_HUMAINS and _FOURNISSEURS_HUMAINS[nom] != fournisseur:
        raise ValueError(f"fournisseur d'outils pour les humains « {nom} » déjà déclaré")
    _FOURNISSEURS_HUMAINS[nom] = fournisseur


def fournisseurs_humains() -> dict[str, FournisseurHumain]:
    return dict(_FOURNISSEURS_HUMAINS)


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
