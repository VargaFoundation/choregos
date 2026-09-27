# SPDX-License-Identifier: Apache-2.0
"""Quelle édition tourne ici, et ce qu'elle s'autorise.

Choregos existe en deux éditions ([ADR 0024](../../../docs/adr/0024-deux-editions.md)) :

* **communautaire** — ce dépôt, Apache-2.0, **une seule organisation** ;
* **entreprise** — propriétaire, dans un dépôt séparé, qui déverrouille le multi-organisation
  *et le finit* (les treize tables encore hors RLS, le cycle de vie d'une organisation, les
  plafonds par organisation).

Le cœur ne connaît pas l'édition entreprise : c'est elle qui se déclare, au démarrage, en
appelant `declarer(ENTREPRISE)`. Tant que personne ne le fait, on est communautaire — et c'est
le bon défaut, parce qu'il est le plus restrictif.

Volontairement **sans clé de licence** : l'édition entreprise est tirée d'un registre privé, et
le contrôle d'accès est l'`imagePullSecret`. Une clé signée hors ligne sera la question du jour
où un client l'hébergera lui-même (ADR 0024, décision 5).
"""

from __future__ import annotations

from typing import Any, Final

COMMUNAUTAIRE: Final = "community"
ENTREPRISE: Final = "enterprise"

#: Un dictionnaire plutôt qu'une variable de module : muter un état partagé par `global` est
#: exactement ce que `ruff` refuse, et un conteneur nommé dit mieux qu'il y a UN état.
_etat: dict[str, object] = {"edition": COMMUNAUTAIRE, "fonctions": frozenset[str]()}


def declarer(nom: str, *, fonctions: frozenset[str] | None = None) -> None:
    """Appelée par l'édition entreprise à son chargement. Le cœur ne l'appelle jamais."""
    if nom not in (COMMUNAUTAIRE, ENTREPRISE):
        raise ValueError(f"édition inconnue : {nom!r}")
    _etat["edition"] = nom
    _etat["fonctions"] = fonctions or frozenset[str]()


#: Ce que le CŒUR sait faire d'une liste de groupes d'IdP : `choregos:<org>:<role>`, et un groupe
#: nu pour l'organisation par défaut. Une organisation, un préfixe.
#:
#: L'édition entreprise en veut un autre — un préfixe PAR organisation, des groupes venus de SAML
#: ou de SCIM, une table de correspondance éditable en interface (§2.2 du plan). Elle le déclare
#: ici, au chargement de son greffon, au lieu de forker `routers/auth.py`.
#:
#: Pourquoi un point d'injection et pas un `Protocol` : il y a UNE décision substituable, avec une
#: signature stable et un appelant. Un protocole `IdentityProvider` complet aurait autant
#: d'implémentations que de spéculations sur ce que l'entreprise voudra — et le reste de son
#: identité (SAML, SCIM, révocation de session) sont des routes NOUVELLES, pas des substitutions.
_MAPPEUR: dict[str, Any] = {}


def declarer_le_mappeur_de_groupes(fonction: Any) -> None:
    """Remplace la traduction groupes d'IdP → rôles par organisation. Le cœur ne l'appelle jamais.

    Signature attendue : `(groups: list[str], default_org: str) -> dict[str, Role]` — la même que
    `routers.auth.roles_des_groupes`, qui reste le défaut.
    """
    _MAPPEUR["fonction"] = fonction


def mappeur_de_groupes(defaut: Any) -> Any:
    """Le mappeur en service : celui qu'un greffon a déclaré, sinon celui du cœur."""
    return _MAPPEUR.get("fonction") or defaut


def courante() -> str:
    return str(_etat["edition"])


def fonctions() -> frozenset[str]:
    valeur = _etat["fonctions"]
    assert isinstance(valeur, frozenset)
    return valeur


def est_entreprise() -> bool:
    return courante() == ENTREPRISE


def reinitialiser() -> None:
    """Pour les tests : revient au défaut le plus restrictif, et au mappeur du cœur."""
    declarer(COMMUNAUTAIRE)
    _MAPPEUR.pop("fonction", None)
