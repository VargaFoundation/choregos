# SPDX-License-Identifier: Apache-2.0
"""The caller's durable conversations (S63-04, ADR 0134 §5): list, read, delete, and talk.

A conversation is its opener's alone: anyone else, and anyone who left its organisation, is answered 404,
and on PostgreSQL a restrictive row-level policy says the same to the database. A conversation is opened
by the service `open_conversation` (the route that opens an assistant's comes with the installations);
a turn is `POST …/messages`, answered whole or as server-sent events (one delta per chunk the model streams).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer
from sse_starlette.sse import EventSourceResponse

from ..db.models import AgentSessionRecord, Organization
from ..db.session import name_the_user, session_scope
from ..deps import Db, Me, Pagination
from ..errors import ApiError, conflict, not_found
from ..rbac import Principal
from ..schemas.agent_sessions import (
    AgentDelta,
    AgentResponse,
    Conversation,
    ConversationMessageCreate,
    ConversationPage,
    ConversationSummary,
)
from ..schemas.base import PageMeta
from ..services.assistant_conversations import require_audience
from ..services.conversation_assistant import with_assistant
from ..services.conversation_turns import cancel_turn, refuse_if_not_allowed
from ..services.conversations import (
    conversation_of,
    converse,
    delete_conversation,
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
        items=await with_assistant(session, window, [summary_of(r) for r in window]),
        meta=PageMeta(next_cursor=cursor, has_more=has_more),
    )


@router.get(
    "/me/conversations/{conversation_id}", response_model=Conversation, operation_id="getMyConversation"
)
async def get_my_conversation(conversation_id: str, session: Db, principal: Me) -> Conversation:
    record = await _mine(session, principal, conversation_id)
    return (await with_assistant(session, [record], [conversation_of(record)]))[0]


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
    # The audience is read again at every turn (S63-15): leaving the group cuts the next one.
    await require_audience(session, principal, record)
    if not body.stream:
        return await converse(session, record, body.input)
    # What a turn refuses first is refused before the stream opens (409, not an event).
    refuse_if_not_allowed(record)
    return EventSourceResponse(_stream_turn(principal, conversation_id, body.input))


async def _stream_turn(
    principal: Principal, conversation_id: str, text: str
) -> AsyncIterator[dict[str, str]]:
    """The turn runs in a session of its own: the request's closes before the stream is read. Each chunk
    the model streams is a `response.output_text.delta`, in the order it came."""
    queue: asyncio.Queue[str | AgentResponse | ApiError] = asyncio.Queue()

    async def on_delta(chunk: str) -> None:
        await queue.put(chunk)

    async def run() -> None:
        try:
            async with session_scope(orgs=list(principal.org_roles)) as session:
                await name_the_user(session, principal.user_id)
                record = await _mine(session, principal, conversation_id)
                await require_audience(session, principal, record)
                await queue.put(await converse(session, record, text, on_delta=on_delta))
        except ApiError as refused:
            await queue.put(refused)

    task = asyncio.ensure_future(run())
    response_id = f"resp_{uuid4().hex}"
    try:
        yield {
            "event": "response.created",
            "data": AgentResponse(
                id=response_id, session_id=conversation_id, status="completed"
            ).model_dump_json(),
        }
        while True:
            item = await queue.get()
            if isinstance(item, str):
                yield {
                    "event": "response.output_text.delta",
                    "data": AgentDelta(response_id=response_id, delta=item).model_dump_json(),
                }
            elif isinstance(item, ApiError):
                yield {
                    "event": "error",
                    "data": json.dumps({"status": item.status_code, "detail": item.detail}),
                }
                return
            else:
                yield {"event": f"response.{item.status}", "data": item.model_dump_json()}
                return
    finally:
        if not task.done():  # the client left: the turn is cancelled, settled and booked
            await cancel_turn(conversation_id)
        await asyncio.gather(task, return_exceptions=True)


@router.post(
    "/me/conversations/{conversation_id}/cancel",
    response_model=ConversationSummary,
    operation_id="cancelMyConversationTurn",
)
async def cancel_my_conversation_turn(
    conversation_id: str, session: Db, principal: Me
) -> ConversationSummary:
    record = await _mine(session, principal, conversation_id)
    if not await cancel_turn(conversation_id, session):
        raise conflict(f"no turn of conversation {conversation_id} is running")
    await session.refresh(record)
    return (await with_assistant(session, [record], [summary_of(record)]))[0]
