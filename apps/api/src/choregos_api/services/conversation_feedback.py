# SPDX-License-Identifier: Apache-2.0
"""A thumb on an answer of a session, and the candidate eval case a thumb down proposes (ADR 0140 §3).

The thumb belongs to the person who opened the session and to the agent revision that answered. A
thumb down also builds a CANDIDATE case from the conversation up to that answer: the person's turns, the
tools the agent called, what it said, the comment — every string pseudonymised
(`choregos_core.pseudonymise`) before it is stored. The candidate is `proposed` to the people who manage
the agent; it enters no suite on its own: nothing here writes an eval file or an agent's spec.
"""

from __future__ import annotations

from typing import Any

from choregos_core.pseudonymise import Pseudonymiser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AgentSessionEvent, AgentSessionRecord, ConversationFeedback, User
from ..errors import unprocessable
from ..schemas.agent_sessions import (
    ConversationCandidate,
    ConversationFeedbackCreate,
    ConversationFeedbackDto,
)

#: A candidate keeps the last turns of a long conversation: the schema allows 20.
MAX_TURNS = 20
PROPOSED = "proposed"


def _names_of(user: User | None) -> list[str]:
    """The person's name, whole and by word: what the conversation may call them."""
    if user is None or not user.display_name.strip():
        return []
    name = user.display_name.strip()
    return [name, *(word for word in name.split() if len(word) >= 3)]


async def _answer_events(session: AsyncSession, record: AgentSessionRecord) -> list[list[AgentSessionEvent]]:
    """The session's events grouped by response: each group ends with its `response.*` event."""
    events = (
        await session.execute(
            select(AgentSessionEvent)
            .where(AgentSessionEvent.session_id == record.id)
            .order_by(AgentSessionEvent.seq)
        )
    ).scalars()
    groups: list[list[AgentSessionEvent]] = [[]]
    for event in events:
        groups[-1].append(event)
        if event.type.startswith("response."):
            groups.append([])
    return [group for group in groups if group and group[-1].type.startswith("response.")]


def candidate_case(
    groups: list[list[AgentSessionEvent]], seq: int, comment: str | None, names: list[str]
) -> dict[str, Any]:
    """The candidate for the `seq`-th answer: a `conversation-evals` case without assertions (an owner
    writes them) and what was observed, every string pseudonymised."""
    turns: list[str] = []
    for group in groups[:seq]:
        turns.extend(
            str(e.payload.get("text") or "")
            for e in group
            if e.type == "input.text" and e.payload.get("text")
        )
    answered = groups[seq - 1]
    tools = [str(e.payload.get("tool")) for e in answered if e.type == "prompt.tool_call"]
    answer = str(answered[-1].payload.get("text") or "")
    pseudonymiser = Pseudonymiser(names)
    case = {
        "name": f"candidate-{seq}",
        "description": "Candidate from a thumb down: an owner decides whether it enters a suite.",
        "turns": [pseudonymiser.text(turn)[:4000] for turn in turns[-MAX_TURNS:]] or ["(no text)"],
    }
    return {
        "case": case,
        "observed": {"tool_calls": tools, "answer": pseudonymiser.text(answer)},
        "comment": pseudonymiser.text(comment) if comment else None,
    }


async def give_feedback(
    session: AsyncSession, record: AgentSessionRecord, body: ConversationFeedbackCreate
) -> ConversationFeedback:
    """Records (or replaces) the opener's thumb on one answer; a thumb down proposes a candidate."""
    seq = body.response if body.response is not None else record.responses
    if seq < 1 or seq > record.responses:
        raise unprocessable(
            f"session {record.id} has {record.responses} answer(s): there is no answer {seq} to rate",
            [{"loc": ["body", "response"], "msg": "no such answer"}],
        )
    row = (
        await session.execute(
            select(ConversationFeedback).where(
                ConversationFeedback.session_id == record.id, ConversationFeedback.response_seq == seq
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = ConversationFeedback(
            org_id=record.org_id,
            session_id=record.id,
            user_id=record.user_id,
            agent_slug=record.agent_slug,
            agent_version=record.agent_version,
            response_seq=seq,
            rating=body.rating,
        )
        session.add(row)
    row.rating = body.rating
    row.comment = body.comment
    if body.rating == "down":
        # A candidate an owner already decided on is not proposed again by a second thumb down.
        if row.candidate_status in (None, PROPOSED):
            names = _names_of(await session.get(User, record.user_id))
            row.candidate = candidate_case(await _answer_events(session, record), seq, body.comment, names)
            row.candidate_status = PROPOSED
    elif row.candidate_status == PROPOSED:
        row.candidate, row.candidate_status = None, None  # the person changed their mind
    await session.flush()
    return row


def feedback_dto(row: ConversationFeedback) -> ConversationFeedbackDto:
    return ConversationFeedbackDto(
        id=row.id,
        session_id=row.session_id,
        agent=row.agent_slug,
        agent_version=row.agent_version,
        response=row.response_seq,
        rating="up" if row.rating == "up" else "down",
        comment=row.comment,
        candidate_status=row.candidate_status,
        created_at=row.created_at,
    )


async def proposed_candidates(
    session: AsyncSession, org_id: str, agent_slug: str, limit: int
) -> list[ConversationCandidate]:
    """The candidates proposed for an agent, newest first. Only the pseudonymised case: never the
    person's raw comment, nor who gave it."""
    rows = (
        await session.execute(
            select(ConversationFeedback)
            .where(
                ConversationFeedback.org_id == org_id,
                ConversationFeedback.agent_slug == agent_slug,
                ConversationFeedback.candidate_status == PROPOSED,
            )
            .order_by(ConversationFeedback.created_at.desc())
            .limit(limit)
        )
    ).scalars()
    return [
        ConversationCandidate(
            id=row.id,
            agent=row.agent_slug,
            agent_version=row.agent_version,
            status="proposed",
            candidate=row.candidate or {},
            created_at=row.created_at,
        )
        for row in rows
    ]
