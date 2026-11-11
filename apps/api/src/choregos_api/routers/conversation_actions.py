# SPDX-License-Identifier: Apache-2.0
"""The writes an assistant proposed in a conversation (S63-10, ADR 0139 point 5): the person decides.

Only the conversation's owner lists, confirms or refuses (anyone else is answered 404). Confirming executes
the action through the broker with the person's identity; the source stays the judge of the right to write."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AgentSessionRecord, Organization
from ..deps import Db, Me
from ..errors import not_found
from ..schemas.conversation_actions import ConversationAction, ConversationActionList
from ..services import conversation_actions as service
from ..services.assistant_conversations import require_audience

router = APIRouter(tags=["agents"])


async def _mine(session: AsyncSession, principal: Me, conversation_id: str) -> AgentSessionRecord:
    """The conversation, for its owner alone: anyone else is answered 404."""
    record = await session.get(AgentSessionRecord, conversation_id)
    organisation = await session.get(Organization, record.org_id) if record is not None else None
    if (
        record is None
        or record.kind != "conversation"
        or record.user_id != principal.user_id
        or organisation is None
        or organisation.slug not in principal.org_roles
    ):
        raise not_found("Conversation", conversation_id)
    return record


@router.get(
    "/me/conversations/{conversation_id}/actions",
    response_model=ConversationActionList,
    operation_id="listMyConversationActions",
)
async def list_actions(conversation_id: str, session: Db, principal: Me) -> ConversationActionList:
    record = await _mine(session, principal, conversation_id)
    rows = await service.actions_of(session, record.id)
    return ConversationActionList(items=[service.to_dto(a) for a in rows])


@router.post(
    "/me/conversations/{conversation_id}/actions/{action_id}/confirm",
    response_model=ConversationAction,
    operation_id="confirmMyConversationAction",
)
async def confirm_action(
    conversation_id: str, action_id: str, session: Db, principal: Me
) -> ConversationAction:
    record = await _mine(session, principal, conversation_id)
    # Leaving the audience cuts what the assistant may do for the person, a write included.
    await require_audience(session, principal, record)
    return service.to_dto(await service.confirm(session, record, action_id, principal))


@router.post(
    "/me/conversations/{conversation_id}/actions/{action_id}/refuse",
    response_model=ConversationAction,
    operation_id="refuseMyConversationAction",
)
async def refuse_action(
    conversation_id: str, action_id: str, session: Db, principal: Me
) -> ConversationAction:
    record = await _mine(session, principal, conversation_id)
    return service.to_dto(await service.refuse(session, record, action_id, principal))
