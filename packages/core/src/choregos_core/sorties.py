# SPDX-License-Identifier: Apache-2.0
"""Codes de sortie du runner (docs/plan/02 §2.2).

`0` signifie « résultat posté », quel que soit le statut de l'étape : c'est l'orchestrateur
qui décide de la suite. Tout autre code est un échec d'infrastructure, rejoué une fois.

Ils vivent dans le cœur parce que deux côtés les lisent : le runner, qui sort avec eux, et
l'exécuteur, qui doit dire POURQUOI un pod est mort quand le runner n'a pas pu le poster lui-même
(un run du scénario RH, le 06/10, ne disait que « Job has reached the specified backoff limit »).
"""

from __future__ import annotations

from enum import IntEnum


class Exit(IntEnum):
    OK = 0
    INPUT_NOT_FOUND = 10
    CLONE_FAILED = 20
    AGENT_UNREACHABLE = 30
    INVALID_RESULT = 40
    #: Une skill de l'agent manque, ou n'a pas l'empreinte que le run attend (ADR 0033).
    SKILLS_INVALID = 50

    @property
    def explanation(self) -> str:
        return {
            Exit.OK: "résultat posté",
            Exit.INPUT_NOT_FOUND: "StageInput introuvable ou résultat déjà posté",
            Exit.CLONE_FAILED: "clone du dépôt impossible",
            Exit.AGENT_UNREACHABLE: "backend agent injoignable",
            Exit.INVALID_RESULT: "résultat invalide après tentatives de réparation",
            Exit.SKILLS_INVALID: "skill absente ou d'empreinte fausse",
        }[self]


def explication(code: int | None) -> str | None:
    """Ce que dit un code de sortie, s'il est l'un des nôtres ; `None` sinon."""
    if code is None:
        return None
    try:
        return Exit(code).explanation
    except ValueError:
        return None
