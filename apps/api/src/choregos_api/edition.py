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

from typing import Final

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


def courante() -> str:
    return str(_etat["edition"])


def fonctions() -> frozenset[str]:
    valeur = _etat["fonctions"]
    assert isinstance(valeur, frozenset)
    return valeur


def est_entreprise() -> bool:
    return courante() == ENTREPRISE


def reinitialiser() -> None:
    """Pour les tests : revient au défaut le plus restrictif."""
    declarer(COMMUNAUTAIRE)
