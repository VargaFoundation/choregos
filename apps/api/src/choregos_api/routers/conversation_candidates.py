# SPDX-License-Identifier: Apache-2.0
"""What an owner does with a candidate case a thumb down proposed (S63-17, ADR 0140 §3): accept it into
the agent's conversation suites by writing its assertions, or dismiss it. Listing them is
`GET /orgs/{org}/agents/{slug}/feedback-candidates` (`routers/agent_sessions.py`)."""

from __future__ import annotations

from fastapi import APIRouter

from ..deps import Db, Me
from ..schemas.agent_sessions import ConversationCandidate, ConversationCandidateAccept
from ..services.agent_access import enforce_decision, load_visible_agent
from ..services.agent_governance import Action, decide
from ..services.conversation_candidates import accept, dismiss

router = APIRouter(tags=["agents"])


@router.post(
    "/orgs/{org}/agents/{slug}/feedback-candidates/{candidate_id}/accept",
    response_model=ConversationCandidate,
    operation_id="acceptAgentFeedbackCandidate",
)
async def accept_agent_feedback_candidate(
    org: str, slug: str, candidate_id: str, body: ConversationCandidateAccept, session: Db, principal: Me
) -> ConversationCandidate:
    """An owner makes a candidate a case of the agent's conversation suites by writing its assertions
    (ADR 0140): it is played with the suites at the promotion gate from then on."""
    organisation, agent, subject = await load_visible_agent(session, principal, org, slug)
    enforce_decision(decide(principal, org, subject, Action.MANAGE))
    return await accept(session, organisation.id, agent, candidate_id, body)


@router.post(
    "/orgs/{org}/agents/{slug}/feedback-candidates/{candidate_id}/dismiss",
    response_model=ConversationCandidate,
    operation_id="dismissAgentFeedbackCandidate",
)
async def dismiss_agent_feedback_candidate(
    org: str, slug: str, candidate_id: str, session: Db, principal: Me
) -> ConversationCandidate:
    """An owner dismisses a candidate: it enters no suite and is not proposed again."""
    organisation, agent, subject = await load_visible_agent(session, principal, org, slug)
    enforce_decision(decide(principal, org, subject, Action.MANAGE))
    return await dismiss(session, organisation.id, agent, candidate_id)
