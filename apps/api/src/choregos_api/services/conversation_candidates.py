# SPDX-License-Identifier: Apache-2.0
"""What an owner does with a candidate case a thumb down proposed (S63-17, ADR 0140 §3): accept it into the
agent's conversation suites, or dismiss it.

A candidate is a case without assertions. Accepting it takes the assertions the owner writes (a case with
none would pass whatever the agent says), validates the whole case as the plugin loader validates a suite
(schema, case names, the assistant's tools) and keeps it on its feedback row as `accepted`: from then on
`installed_suites` plays it with the plugin's cases, in the gate. A decided candidate is final.
"""

from __future__ import annotations

from typing import Any, Literal

from choregos_core.plugins import suite_problems
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Agent, ConversationFeedback
from ..errors import conflict, not_found, unprocessable
from ..schemas.agent_sessions import ConversationCandidate, ConversationCandidateAccept
from . import assistants
from .conversation_feedback import PROPOSED
from .installed_suites import ACCEPTED, accepted_cases, suites_of, wrapping

DISMISSED = "dismissed"


def candidate_dto(row: ConversationFeedback) -> ConversationCandidate:
    status: Literal["proposed", "accepted", "dismissed"] = (
        "accepted"
        if row.candidate_status == ACCEPTED
        else "dismissed"
        if row.candidate_status == DISMISSED
        else "proposed"
    )
    return ConversationCandidate(
        id=row.id,
        agent=row.agent_slug,
        agent_version=row.agent_version,
        status=status,
        candidate=row.candidate or {},
        created_at=row.created_at,
    )


async def _proposed(
    session: AsyncSession, org_id: str, agent: Agent, candidate_id: str
) -> ConversationFeedback:
    row = (
        await session.execute(
            select(ConversationFeedback).where(
                ConversationFeedback.id == candidate_id,
                ConversationFeedback.org_id == org_id,
                ConversationFeedback.agent_slug == agent.slug,
                ConversationFeedback.candidate.is_not(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise not_found("Candidate", candidate_id)
    if row.candidate_status != PROPOSED:
        raise conflict(f"candidate {candidate_id} is already {row.candidate_status}")
    return row


async def dismiss(
    session: AsyncSession, org_id: str, agent: Agent, candidate_id: str
) -> ConversationCandidate:
    row = await _proposed(session, org_id, agent, candidate_id)
    row.candidate_status = DISMISSED
    await session.flush()
    return candidate_dto(row)


async def accept(
    session: AsyncSession, org_id: str, agent: Agent, candidate_id: str, body: ConversationCandidateAccept
) -> ConversationCandidate:
    row = await _proposed(session, org_id, agent, candidate_id)
    candidate: dict[str, Any] = dict(row.candidate or {})
    case: dict[str, Any] = {
        **dict(candidate.get("case") or {}),
        "name": body.name or f"feedback-{row.id[:8]}",
        "assert": body.assertions,
    }
    if body.tool_results:
        case["tool_results"] = body.tool_results
    if body.context:
        case["context"] = body.context
    suite = {"kind": "conversation", "agent": agent.slug, "threshold": 1, "cases": [case]}
    installations = await wrapping(session, agent)
    problems: list[str] = []
    documents = [await assistants.manifest(session, installation) for installation in installations]
    for document in documents or [None]:
        problems += suite_problems(suite, "case", document)
    taken = {str(c["name"]) for c in await accepted_cases(session, org_id, agent.slug)}
    for installation in installations:
        taken |= {
            str(c.get("name"))
            for _, suite_ in await suites_of(session, installation)
            for c in suite_["cases"]
        }
    if case["name"] in taken:
        problems.append(f"name: a case named `{case['name']}` is already in the agent's suites")
    if problems:
        raise unprocessable(
            "the case is not a valid conversation case for this agent",
            [{"loc": ["body"], "msg": problem} for problem in sorted(set(problems))],
        )
    row.candidate = {**candidate, "case": case}
    row.candidate_status = ACCEPTED
    await session.flush()
    return candidate_dto(row)
