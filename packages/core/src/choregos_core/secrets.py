# SPDX-License-Identifier: Apache-2.0
"""Les références de secret (ADR 0034) — la couture `SecretResolver`.

Un connecteur ne porte jamais un secret en clair : sa configuration nomme une RÉFÉRENCE
(`env:JIRA_TOKEN`), que la plateforme résout quand elle construit l'adaptateur — dans le processus
qui en a besoin, jamais dans la base, jamais dans la réponse d'une API. Le cœur sait lire
l'environnement (`env:`) ; un greffon déclare les autres schémas — un coffre (`vault:`,
`infisical:`) — par `declarer_un_resolveur`.

`connectors.secret_ref` existait depuis le schéma initial, stocké et jamais lu : un secret
« configuré » n'atteignait aucun adaptateur, qui retombait en silence sur sa valeur par défaut.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable

#: `schéma:reste` — un schéma en minuscules, puis ce que son résolveur sait lire.
_REFERENCE = re.compile(r"^([a-z][a-z0-9-]{1,31}):(.+)$")


class SecretIntrouvable(LookupError):  # noqa: N818 - un manque nommé, rendu tel quel
    """La référence est bien formée, mais rien ne la résout ici."""


class ReferenceInvalide(ValueError):  # noqa: N818 - un refus motivé, rendu en 422
    """Ce n'est pas une référence de secret qu'un résolveur déclaré sait lire."""


def _environnement(nom: str) -> str:
    if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", nom):
        raise ReferenceInvalide(f"`env:{nom}` : un nom de variable en MAJUSCULES est attendu")
    valeur = os.environ.get(nom)
    if not valeur:
        raise SecretIntrouvable(f"`env:{nom}` : la variable n'est pas posée dans ce processus")
    return valeur


_RESOLVEURS: dict[str, Callable[[str], str]] = {"env": _environnement}


def declarer_un_resolveur(schema: str, resoudre_: Callable[[str], str]) -> None:
    """Appelée par un greffon à son chargement : `schema` (`vault`, `infisical`…) et sa lecture."""
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,31}", schema):
        raise ValueError(f"schéma de secret invalide : {schema!r}")
    if schema in _RESOLVEURS and _RESOLVEURS[schema] is not resoudre_:
        raise ValueError(f"schéma de secret déjà déclaré : {schema}")
    _RESOLVEURS[schema] = resoudre_


def schemas() -> frozenset[str]:
    return frozenset(_RESOLVEURS)


def verifier(reference: str) -> None:
    """Refuse ce qui n'est pas une référence lisible ici — sans la résoudre : l'API qui
    l'enregistre n'a pas forcément le secret, l'orchestrateur qui s'en sert, si."""
    m = _REFERENCE.match(reference or "")
    if m is None:
        raise ReferenceInvalide(
            "une référence de secret est attendue (`env:NOM`), pas une valeur : un secret ne "
            "s'écrit jamais en clair"
        )
    if m.group(1) not in _RESOLVEURS:
        raise ReferenceInvalide(
            f"schéma de secret inconnu : `{m.group(1)}` (connus : {', '.join(sorted(_RESOLVEURS))})"
        )


def resoudre(reference: str) -> str:
    verifier(reference)
    m = _REFERENCE.match(reference)
    assert m is not None
    return _RESOLVEURS[m.group(1)](m.group(2))


def reinitialiser() -> None:
    """Pour les tests : seul le résolveur du cœur reste."""
    _RESOLVEURS.clear()
    _RESOLVEURS["env"] = _environnement


__all__ = [
    "ReferenceInvalide",
    "SecretIntrouvable",
    "declarer_un_resolveur",
    "reinitialiser",
    "resoudre",
    "schemas",
    "verifier",
]
