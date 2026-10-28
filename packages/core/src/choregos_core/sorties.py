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
    #: A declared input does not hash to the digest the platform recorded (ADR 0049).
    INPUTS_INVALID = 60
    #: A knowledge base of the agent was not delivered, or not with the digest the run expects (ADR 0051).
    KNOWLEDGE_INVALID = 51
    #: The gateway refused the run's key: its budget is spent (S27-01). The result is posted.
    BUDGET_EXCEEDED = 70
    #: The context reduction the run requires did not start, and the policy says `fail` (ADR 0055).
    CONTEXT_UNAVAILABLE = 80
    #: The kernel cannot confine the agent and the deployment says `enforce` (ADR 0094).
    CONFINEMENT_UNAVAILABLE = 90

    @property
    def explanation(self) -> str:
        return {
            Exit.OK: "result posted",
            Exit.INPUT_NOT_FOUND: "StageInput not found, or result already posted",
            Exit.CLONE_FAILED: "could not clone the repository",
            Exit.AGENT_UNREACHABLE: "agent backend unreachable",
            Exit.INVALID_RESULT: "invalid result after repair attempts",
            Exit.SKILLS_INVALID: "skill missing, or its digest does not match",
            Exit.INPUTS_INVALID: "an input artifact does not match its digest",
            Exit.KNOWLEDGE_INVALID: "knowledge base missing, or its digest does not match",
            Exit.BUDGET_EXCEEDED: "the gateway refused the run's key: its budget is spent",
            Exit.CONTEXT_UNAVAILABLE: "the context reduction the policy requires did not start",
            Exit.CONFINEMENT_UNAVAILABLE: "confinement required, the kernel cannot confine the agent",
        }[self]


def explication(code: int | None) -> str | None:
    """Ce que dit un code de sortie, s'il est l'un des nôtres ; `None` sinon."""
    if code is None:
        return None
    try:
        return Exit(code).explanation
    except ValueError:
        return None
