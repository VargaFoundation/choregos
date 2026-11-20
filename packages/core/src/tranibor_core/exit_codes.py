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
    #: The deployment says the agent runs in its own container and the runner found no launcher
    #: there: it never starts the agent as its own child instead (ADR 0146 §9).
    AGENT_ISOLATION_UNAVAILABLE = 100
    #: A file the runner writes into the agent's tree stands behind a link or a second name the agent left
    #: (a workspace kept across a person's answer): the runner follows neither and stops (S65-04b).
    WORKSPACE_UNSAFE = 110
    #: `TRANIBOR_FAKES` is on and `TRANIBOR_ENV` is staging or prod: the runner does not start (S80-03).
    FAKES_IN_PRODUCTION = 120
    #: The egress gate (S80-07f): a direct connection out of the pod still succeeded at its deadline,
    #: so the pod's egress NetworkPolicy is not in force and nothing the agent controls is started.
    #: The gate's exit, read on its init container (`tranibor_core.egress_gate`).
    EGRESS_OPEN = 115

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
            Exit.AGENT_ISOLATION_UNAVAILABLE: "the agent's own container is required and has no launcher",
            Exit.WORKSPACE_UNSAFE: "a link or a second name in the workspace stands where the runner writes",
            Exit.FAKES_IN_PRODUCTION: "the fakes are on in staging or prod: the runner refuses to start",
            Exit.EGRESS_OPEN: "the pod still reached the outside directly: its egress policy is not in force",
        }[self]


def explanation(code: int | None) -> str | None:
    """What an exit code says, if it is one of ours; `None` otherwise."""
    if code is None:
        return None
    try:
        return Exit(code).explanation
    except ValueError:
        return None
