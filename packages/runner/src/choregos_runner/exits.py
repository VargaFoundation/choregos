"""Codes de sortie du runner (docs/plan/02 §2.2).

`0` signifie « résultat posté », quel que soit le statut de l'étape : c'est l'orchestrateur
qui décide de la suite. Tout autre code est un échec d'infrastructure, rejoué une fois.
"""

from __future__ import annotations

from enum import IntEnum


class Exit(IntEnum):
    OK = 0
    INPUT_NOT_FOUND = 10
    CLONE_FAILED = 20
    AGENT_UNREACHABLE = 30
    INVALID_RESULT = 40

    @property
    def explanation(self) -> str:
        return {
            Exit.OK: "résultat posté",
            Exit.INPUT_NOT_FOUND: "StageInput introuvable ou résultat déjà posté",
            Exit.CLONE_FAILED: "clone du dépôt impossible",
            Exit.AGENT_UNREACHABLE: "backend agent injoignable",
            Exit.INVALID_RESULT: "résultat invalide après tentatives de réparation",
        }[self]
