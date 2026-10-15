# SPDX-License-Identifier: Apache-2.0
"""Les références de secret (ADR 0034) — la couture `SecretResolver`.

Un connecteur ne porte jamais un secret en clair : sa configuration nomme une RÉFÉRENCE
(`env:CONNECTOR_JIRA_TOKEN`), which the platform resolves when it builds the adapter, in the process
that needs it, jamais dans la base, jamais dans la réponse d'une API. Le cœur sait lire
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


class VariableRefusee(ReferenceInvalide):
    """`env:NAME` names a variable outside the allowlist (S39-06b)."""


#: Which variables an `env:NAME` reference may read (S39-06b). A reference is written by a tenant
#: role and resolved by the API or the orchestrator, whose environment also holds the platform's own
#: secrets (session key, gateway master key, database password): a name outside this pattern is
#: refused at write and again at resolution. A deployment widens it with this variable.
PATTERN_ENV = "CHOREGOS_SECRET_ENV_PATTERN"
PATTERN_PAR_DEFAUT = r"^CONNECTOR_[A-Z0-9_]+$"

#: Never readable through a reference, whatever the pattern says: the platform's own variables.
_TOUJOURS_REFUSEES = re.compile(r"^(CHOREGOS_|DATABASE_|PG|AWS_|KUBERNETES_|TEMPORAL_)")


def _pattern() -> str:
    return os.environ.get(PATTERN_ENV) or PATTERN_PAR_DEFAUT


def _verifier_le_nom(nom: str) -> None:
    if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", nom):
        raise ReferenceInvalide(f"`env:{nom}`: an UPPERCASE variable name is expected")
    pattern = _pattern()
    try:
        allowed = re.search(pattern, nom) is not None
    except re.error as erreur:
        raise ReferenceInvalide(f"{PATTERN_ENV} is not a valid pattern: {erreur}") from erreur
    if _TOUJOURS_REFUSEES.match(nom) or not allowed:
        raise VariableRefusee(f"env:NAME: only variables matching {pattern} can be referenced")


def _environnement(nom: str) -> str:
    _verifier_le_nom(nom)
    valeur = os.environ.get(nom)
    if not valeur:
        raise SecretIntrouvable(f"`env:{nom}`: the variable is not set in this process")
    return valeur


_RESOLVEURS: dict[str, Callable[[str], str]] = {"env": _environnement}


def declarer_un_resolveur(schema: str, resoudre_: Callable[[str], str]) -> None:
    """Appelée par un greffon à son chargement : `schema` (`vault`, `infisical`…) et sa lecture."""
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,31}", schema):
        raise ValueError(f"invalid secret scheme: {schema!r}")
    if schema in _RESOLVEURS and _RESOLVEURS[schema] is not resoudre_:
        raise ValueError(f"secret scheme already declared: {schema}")
    _RESOLVEURS[schema] = resoudre_


def schemas() -> frozenset[str]:
    return frozenset(_RESOLVEURS)


def verifier(reference: str) -> None:
    """Refuse ce qui n'est pas une référence lisible ici — sans la résoudre : l'API qui
    l'enregistre n'a pas forcément le secret, l'orchestrateur qui s'en sert, si."""
    m = _REFERENCE.match(reference or "")
    if m is None:
        raise ReferenceInvalide(
            "a secret reference is expected (`env:NAME`), not a value: a secret is never written in clear"
        )
    if m.group(1) not in _RESOLVEURS:
        raise ReferenceInvalide(
            f"unknown secret scheme: `{m.group(1)}` (known: {', '.join(sorted(_RESOLVEURS))})"
        )
    if m.group(1) == "env":
        _verifier_le_nom(m.group(2))


def resoudre(reference: str) -> str:
    try:
        verifier(reference)
    except VariableRefusee as refus:
        # A reference written before the allowlist (or by hand in the database) puts its connector
        # in error, like any reference nothing resolves: it never reads the variable.
        raise SecretIntrouvable(str(refus)) from refus
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
    "VariableRefusee",
    "declarer_un_resolveur",
    "reinitialiser",
    "resoudre",
    "schemas",
    "verifier",
]
