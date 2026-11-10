# SPDX-License-Identifier: Apache-2.0
"""The caller's durable conversations (S63-04, ADR 0134 §5): list, read, delete, and talk.

A conversation is its opener's alone: anyone else, and anyone who left its organisation, is answered 404,
and on PostgreSQL a restrictive row-level policy says the same to the database. A conversation is opened
by the service `open_conversation` (the route that opens an assistant's comes with the installations);
a turn is `POST …/messages`, answered whole or as server-sent events (the text in one delta: the prompt
loop does not stream yet).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer
from sse_starlette.sse import EventSourceResponse

from ..db.models import AgentSessionRecord, Organization
from ..deps import Db, Me, Pagination
from ..errors import not_found
from ..schemas.agent_sessions import (
    AgentResponse,
    Conversation,
    ConversationMessageCreate,
    ConversationPage,
)
from ..schemas.base import PageMeta
from ..services.conversations import (
    conversation_of,
    converse,
    delete_conversation,
    response_events,
    summary_of,
)

router = APIRouter(tags=["agents"])


async def _mine(session: AsyncSession, principal: Me, conversation_id: str) -> AgentSessionRecord:
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


@router.get("/me/conversations", response_model=ConversationPage, operation_id="listMyConversations")
async def list_my_conversations(session: Db, principal: Me, page: Pagination) -> ConversationPage:
    rows = (
        (
            await session.execute(
                select(AgentSessionRecord)
                .join(Organization, Organization.id == AgentSessionRecord.org_id)
                .where(
                    AgentSessionRecord.kind == "conversation",
                    AgentSessionRecord.user_id == principal.user_id,
                    Organization.slug.in_(list(principal.org_roles)),
                )
                .options(
                    defer(AgentSessionRecord.messages),
                    defer(AgentSessionRecord.context),
                    defer(AgentSessionRecord.stage_input),
                )
                .order_by(
                    func.coalesce(AgentSessionRecord.last_activity_at, AgentSessionRecord.created_at).desc(),
                    AgentSessionRecord.id,
                )
            )
        )
        .scalars()
        .all()
    )
    window, cursor, has_more = page.slice(list(rows))
    return ConversationPage(
        items=[summary_of(r) for r in window], meta=PageMeta(next_cursor=cursor, has_more=has_more)
    )


@router.get(
    "/me/conversations/{conversation_id}", response_model=Conversation, operation_id="getMyConversation"
)
async def get_my_conversation(conversation_id: str, session: Db, principal: Me) -> Conversation:
    return conversation_of(await _mine(session, principal, conversation_id))


@router.delete(
    "/me/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteMyConversation",
)
async def delete_my_conversation(conversation_id: str, session: Db, principal: Me) -> Response:
    await delete_conversation(session, await _mine(session, principal, conversation_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/me/conversations/{conversation_id}/messages",
    response_model=AgentResponse,
    operation_id="sendMyConversationMessage",
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def send_my_conversation_message(
    conversation_id: str, body: ConversationMessageCreate, session: Db, principal: Me
) -> Any:
    record = await _mine(session, principal, conversation_id)
    answer = await converse(session, record, body.input)
    if not body.stream:
        return answer
    return EventSourceResponse(response_events(answer))
