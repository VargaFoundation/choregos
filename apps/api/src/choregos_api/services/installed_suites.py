# SPDX-License-Identifier: Apache-2.0
"""The conversation suites of the assistants an organisation installed reach the promotion gate (S63-17).

An installation names, through its manifest, the eval files of its plugin (`evals`); each is a conversation
suite for the agent the assistant wraps (ADR 0140). `installed_suite_source` is the gate's
`ConversationSuiteSource` (`conversation_gate.set_conversation_source`): for a version of an agent it finds
the installations in the agent's organisation that wrap it, reads their suites from the installed plugin, has
a
`SuitePlayer` play them, and hands the outcomes to the gate, which refuses production below a suite's
threshold.

WHO plays a suite is a `SuitePlayer`, registered with `set_suite_player`: tests and CI register one that
plays on a scripted model (`scripted_player`). With none registered the DEFAULT player plays on the platform's
model gateway (`conversation_player.default_player`), the rubric scored by the judge model.
A suite that cannot
be played (no model configured, a model refusal, a player answering `None`) fails the gate with the reason,
because nothing evaluated is never a pass (ADR 0020); only `conversation_suites_require_model = false` lets
such a suite add no check.

A candidate case the owners accept (`services/conversation_candidates`) is kept on its feedback row
(`accepted`) and joins the suites of the agent it was given for, played with the plugin's own cases.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import httpx
import yaml
from choregos_core.conversation_evals import SuiteResult, loop_tools, play_suite, tools_named
from choregos_core.conversation_judge import judge_with
from choregos_core.prompt_loop import Complete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import Agent, AssistantInstallation, ConversationFeedback
from . import assistants, plugin_files
from .conversation_gate import SuiteOutcome
from .conversation_player import CannotPlayError, ModelRefusedError, default_player, judge_model_of

ACCEPTED = "accepted"

#: (session, installation, the suite's name, the suite with its accepted additions, the agent, its version).
#: It raises `CannotPlayError` when the suite cannot be played here.
SuitePlayer = Callable[
    [AsyncSession, AssistantInstallation, str, dict[str, Any], Agent, int], Awaitable[SuiteResult | None]
]

_PLAYER: list[SuitePlayer] = []


def set_suite_player(player: SuitePlayer | None) -> None:
    """Who plays the installed suites; `None` goes back to the default, which plays on the gateway."""
    _PLAYER[:] = [player] if player is not None else []


def current_player() -> SuitePlayer:
    """The registered player, else `conversation_player.default_player`: never none (a hole in production)."""
    return _PLAYER[0] if _PLAYER else default_player


def scripted_player(
    complete_for: Callable[[int, str], Complete],
    instructions: str = "scripted",
    judge: Complete | None = None,
) -> SuitePlayer:
    """A player on scripted models: `complete_for(version, case name)` gives each case its model; `judge`
    is the model that scores the rubrics (none: a case with a rubric fails, nothing was judged)."""

    async def play(
        session: AsyncSession,
        row: AssistantInstallation,
        name: str,
        suite: dict[str, Any],
        agent: Agent,
        version: int,
    ) -> SuiteResult | None:
        names = sorted({tool for case in suite.get("cases") or [] for _, tool in tools_named(case)})
        return await play_suite(
            suite,
            instructions=instructions,
            tools=loop_tools(names),
            complete_for=lambda case: complete_for(version, case),
            judge=judge_with(judge, judge_model_of(get_settings())) if judge is not None else None,
        )

    return play


async def accepted_cases(session: AsyncSession, org_id: str, agent_slug: str) -> list[dict[str, Any]]:
    """The cases an owner accepted from a thumb down (`conversation_feedback`, `accepted`): a stored addition
    to the agent's conversation suites, played with the plugin's own cases."""
    rows = (
        (
            await session.execute(
                select(ConversationFeedback)
                .where(
                    ConversationFeedback.org_id == org_id,
                    ConversationFeedback.agent_slug == agent_slug,
                    ConversationFeedback.candidate_status == ACCEPTED,
                )
                .order_by(ConversationFeedback.created_at, ConversationFeedback.id)
            )
        )
        .scalars()
        .all()
    )
    return [
        dict(row.candidate["case"])
        for row in rows
        if isinstance(row.candidate, dict) and row.candidate.get("case")
    ]


async def with_additions(
    session: AsyncSession, suite: dict[str, Any], row: AssistantInstallation
) -> dict[str, Any]:
    """The plugin's suite plus the cases the owners accepted for its agent (never replacing one by name)."""
    taken = {str(c.get("name")) for c in suite.get("cases") or []}
    extra = [
        c
        for c in await accepted_cases(session, row.org_id, str(suite.get("agent")))
        if c["name"] not in taken
    ]
    return {**suite, "cases": [*(suite.get("cases") or []), *extra]} if extra else suite


async def suites_of(session: AsyncSession, row: AssistantInstallation) -> list[tuple[str, dict[str, Any]]]:
    """The conversation suites an installation registers: its manifest's eval files, read from the plugin
    it installed, with the cases its owners accepted."""
    document = await assistants.manifest(session, row)
    if document is None:
        return []
    found: list[tuple[str, dict[str, Any]]] = []
    for ref in document.get("evals") or []:
        text = await plugin_files.plugin_text(session, row.org_id, row.plugin, str(ref))
        parsed: Mapping[str, Any] | None = yaml.safe_load(text) if text else None
        if isinstance(parsed, Mapping) and parsed.get("kind") == "conversation":
            found.append((str(ref), await with_additions(session, dict(parsed), row)))
    return found


async def wrapping(session: AsyncSession, agent: Agent) -> list[AssistantInstallation]:
    """The installations of the agent's organisation whose assistant wraps it."""
    rows = (
        (
            await session.execute(
                select(AssistantInstallation).where(AssistantInstallation.org_id == agent.org_id)
            )
        )
        .scalars()
        .all()
    )
    wrapped: list[AssistantInstallation] = []
    for row in rows:
        document = await assistants.manifest(session, row)
        if document is not None and document.get("agent") == agent.slug:
            wrapped.append(row)
    return wrapped


async def installed_suite_source(session: AsyncSession, agent: Agent, version: int) -> list[SuiteOutcome]:
    """The gate's source: the suites of every installation that wraps `agent`, and how `version` fared."""
    player = current_player()
    outcomes: list[SuiteOutcome] = []
    for row in await wrapping(session, agent):
        for ref, suite in await suites_of(session, row):
            label = f"{row.slug}:{ref}"
            try:
                outcomes.append(SuiteOutcome(label, await player(session, row, ref, suite, agent, version)))
            except CannotPlayError as cannot:
                if get_settings().conversation_suites_require_model:
                    outcomes.append(SuiteOutcome(label, None, str(cannot)))
            except (ModelRefusedError, httpx.HTTPError) as failure:
                outcomes.append(SuiteOutcome(label, None, f"the model could not be called: {failure}"[:300]))
    return outcomes
