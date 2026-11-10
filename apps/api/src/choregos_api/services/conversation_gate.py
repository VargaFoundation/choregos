# SPDX-License-Identifier: Apache-2.0
"""A conversation suite counts in promotion (ADR 0140 §2, amending ADR 0071).

An agent version that an assistant wraps does not reach `production` while the assistant's conversation
suites (`conversation-evals.schema.json`) pass below their `threshold`: `checks_for` adds the required check
`conversation_suite`, exactly as it adds `eval_suite` for the agent's own cases.

WHICH suites wrap an agent version is the installation's to say (S63-02: the assistant an organisation
installed, its plugin's eval files, the revision of the agent it wraps). Until installations hand them
over, the gate takes them from a `ConversationSuiteSource`, registered with `set_conversation_source`; no
source, or a source that finds no suite for the agent, adds no check (an agent no assistant wraps is
unaffected). The source returns each suite with its outcome (`choregos_core.conversation_evals.SuiteResult`)
or `None` when the suite was never evaluated for that version, which fails the gate: nothing evaluated is
never a pass (ADR 0020).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from choregos_core.conversation_evals import SuiteResult
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Agent
from ..schemas import AgentReleaseCheck

CHECK = "conversation_suite"


@dataclass(frozen=True)
class SuiteOutcome:
    """A suite of an assistant that wraps the agent, and how this version fared on it."""

    name: str
    result: SuiteResult | None


ConversationSuiteSource = Callable[[AsyncSession, Agent, int], Awaitable[list[SuiteOutcome]]]

_SOURCE: list[ConversationSuiteSource] = []


def set_conversation_source(source: ConversationSuiteSource | None) -> None:
    """Where the gate finds the suites that wrap an agent version (the installation's, S63-02); `None`
    removes it."""
    _SOURCE[:] = [source] if source is not None else []


def judge(outcomes: list[SuiteOutcome]) -> AgentReleaseCheck | None:
    """The gate's verdict on the suites' outcomes; `None` when no suite wraps the version."""
    if not outcomes:
        return None
    problems: list[str] = []
    for outcome in outcomes:
        result = outcome.result
        if result is None:
            problems.append(f"{outcome.name}: not evaluated for this version")
        elif not result.passed:
            problems.append(
                f"{outcome.name}: {result.share:.0%} of cases pass, {result.threshold:.0%} required "
                f"(failing: {', '.join(result.failing)})"
            )
    detail = "; ".join(problems) if problems else f"{len(outcomes)} conversation suite(s) pass"
    return AgentReleaseCheck(name=CHECK, required=True, passed=not problems, detail=detail)


async def conversation_check(session: AsyncSession, agent: Agent, version: int) -> AgentReleaseCheck | None:
    """The `conversation_suite` check of releasing `version` of `agent` to production, if a suite wraps it."""
    if not _SOURCE:
        return None
    return judge(await _SOURCE[0](session, agent, version))
