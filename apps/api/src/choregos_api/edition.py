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

    ⚠️ **Un mappeur de remplacement doit DÉLÉGUER au mappeur du cœur pour ce qu'il ne comprend
    pas**, au lieu de rendre un dictionnaire vide. Il remplace la traduction ENTIÈRE : un mappeur
    qui ne lit que `<org>/<role>` fait perdre au déploiement ses connexions en
    `choregos:<org>:<role>` — y compris celle de l'amorçage et celle du développement. Constaté en
    éprouvant cette couture avec un greffon installé pour de vrai : `403 … il manque ['varga']` sur
    la création d'une organisation, et la cause n'était pas là où le message pointait.

        def le_mien(groups, default_org):
            roles = dict(roles_des_groupes(groups, default_org))   # d'abord le cœur
            ...                                                    # puis les formes maison
            return roles
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


class SessionRefusee(Exception):  # noqa: N818 - une décision, pas une erreur
    """Une validation de session refuse : `str()` est la raison, rendue en 401."""


#: Les validations de session déclarées par les greffons, et les autorités qui désignent un
#: administrateur de la plateforme. Deux coutures d'IDENTITÉ, comme le mappeur de groupes.
_VALIDATIONS_DE_SESSION: list[Any] = []
_ADMINISTRATEURS_DE_PLATEFORME: list[Any] = []


def declarer_une_validation_de_session(fonction: Any) -> None:
    """Une session signée est valable jusqu'à `exp` : le cœur ne sait pas la révoquer avant.

    Un greffon qui le veut — révocation côté serveur, déprovisionnement SCIM — déclare ici
    `fonction(session_db, user, payload)`, synchrone ou asynchrone, jouée à CHAQUE requête
    authentifiée par cookie. Elle lève `SessionRefusee(raison)` pour refuser (401 avec la raison).
    Toute autre exception remonte telle quelle : une validation en panne ne vaut pas acceptation.
    """
    if fonction not in _VALIDATIONS_DE_SESSION:
        _VALIDATIONS_DE_SESSION.append(fonction)


def validations_de_session() -> tuple[Any, ...]:
    return tuple(_VALIDATIONS_DE_SESSION)


def declarer_un_administrateur_de_plateforme(fonction: Any) -> None:
    """Le cœur tient pour administrateur de la plateforme l'`org_admin` de TOUTES les
    organisations (`deps.exiger_admin_de_plateforme`). En multi-organisation, un vrai rôle de
    plateforme est plus juste : un greffon déclare ici `fonction(session_db, principal) -> bool`,
    synchrone ou asynchrone.

    ADDITIVE : elle peut ACCORDER le droit à qui la règle du cœur le refuse, jamais le retirer à qui
    elle l'accorde — une installation qui charge un greffon garde au moins ses administrateurs.
    """
    if fonction not in _ADMINISTRATEURS_DE_PLATEFORME:
        _ADMINISTRATEURS_DE_PLATEFORME.append(fonction)


def administrateurs_de_plateforme() -> tuple[Any, ...]:
    return tuple(_ADMINISTRATEURS_DE_PLATEFORME)


def reinitialiser() -> None:
    """Pour les tests : revient au défaut le plus restrictif, et aux règles du cœur."""
    declarer(COMMUNAUTAIRE)
    _MAPPEUR.pop("fonction", None)
    _VALIDATIONS_DE_SESSION.clear()
    _ADMINISTRATEURS_DE_PLATEFORME.clear()
