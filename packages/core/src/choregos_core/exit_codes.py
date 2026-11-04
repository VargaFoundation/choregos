# SPDX-License-Identifier: Apache-2.0
"""Exit codes of the runner (docs/plan/02 §2.2).

`0` means "result posted", whatever the status of the stage: the orchestrator decides what
follows. Any other code is an infrastructure failure, replayed once.

They live in the core because two sides read them: the runner, which exits with them, and the
executor, which must say WHY a pod died when the runner could not post it itself (a run of the
HR scenario, on 06/10, said only "Job has reached the specified backoff limit").
"""

from __future__ import annotations

from enum import IntEnum


class Exit(IntEnum):
    OK = 0
    INPUT_NOT_FOUND = 10
    CLONE_FAILED = 20
    AGENT_UNREACHABLE = 30
    INVALID_RESULT = 40
    #: A skill of the agent is missing, or does not have the digest the run expects (ADR 0033).
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


def explanation(code: int | None) -> str | None:
    """What an exit code says, if it is one of ours; `None` otherwise."""
    if code is None:
        return None
    try:
        return Exit(code).explanation
    except ValueError:
        return None
