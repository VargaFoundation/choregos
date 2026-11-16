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
from typing import Annotated, Any
from uuid import uuid4

from choregos_core import utcnow
from choregos_core.domain import aware
from fastapi import APIRouter, Header, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer
from sse_starlette.sse import EventSourceResponse

from ..db.models import AgentSessionRecord, Organization
from ..db.session import release_connection
from ..deps import Db, Me, Pagination
from ..errors import ApiError, conflict
from ..schemas.agent_sessions import (
    AgentResponse,
    Conversation,
    ConversationMessageCreate,
    ConversationPage,
    ConversationSummary,
)
from ..schemas.base import PageMeta
from ..services.assistant_conversations import require_audience
from ..services.conversation_assistant import with_assistant
from ..services.conversation_signals import with_signals
from ..services.conversation_turns import cancel_turn, refuse_if_not_allowed
from ..services.conversations import (
    conversation_of,
    converse,
    delete_conversation,
    summary_of,
)
from ..services.my_conversations import mine
from ..services.turn_follow import TurnRun, is_running, position_after, run_of, start_turn

router = APIRouter(tags=["agents"])


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
    record = await mine(session, principal, conversation_id)
    return (await with_assistant(session, [record], [conversation_of(record)]))[0]


@router.delete(
    "/me/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteMyConversation",
)
async def delete_my_conversation(conversation_id: str, session: Db, principal: Me) -> Response:
    await delete_conversation(session, await mine(session, principal, conversation_id))
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
    record = await mine(session, principal, conversation_id)
    # The audience is read again at every turn (S63-15): leaving the group cuts the next one.
    await require_audience(session, principal, record)
    if not body.stream and not body.detach:
        return await with_signals(session, await converse(session, record, body.input))
    # What a turn refuses first is refused before anything starts (409, not an event).
    refuse_if_not_allowed(record)
    if is_running(conversation_id):
        raise conflict(f"a turn of conversation {conversation_id} is already running")
    await release_connection(session)  # the turn reads the conversation from a session of its own
    run = start_turn(principal, conversation_id, body.input)
    if body.stream:
        return EventSourceResponse(_sent(run, detach=body.detach))
    return await _answered(run)


async def _answered(run: TurnRun) -> AgentResponse:
    """The whole answer of a detached turn, for a client that stayed: what the stream's last event carries.
    The turn is shielded: a client that left does not stop it."""
    assert run.task is not None
    await asyncio.shield(asyncio.wait({run.task}))
    name, data = run.events[-1]
    if name == "error":
        detail = json.loads(data)
        raise ApiError(int(detail["status"]), "Turn refused", str(detail["detail"]))
    return AgentResponse.model_validate_json(data)


async def _sent(run: TurnRun, *, detach: bool) -> AsyncIterator[dict[str, str]]:
    """The turn the sender asked to stream. When the client leaves, the turn is cancelled, settled and
    booked, unless it was `detach`ed: then it runs on and is booked once, when it ends."""
    try:
        async for event in run.follow():
            yield event
    finally:
        # The turn may not have registered yet (it is cancellable once `converse` runs): then the task is.
        if not detach and not run.finished and not await cancel_turn(run.conversation_id) and run.task:
            run.task.cancel()
        if run.task is not None and not detach:
            await asyncio.gather(run.task, return_exceptions=True)


@router.get(
    "/me/conversations/{conversation_id}/events",
    operation_id="followMyConversationTurn",
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def follow_my_conversation_turn(
    conversation_id: str,
    session: Db,
    principal: Me,
    after_seq: Annotated[int | None, Query(ge=0)] = None,
    last_event_id: Annotated[str | None, Header()] = None,
) -> Any:
    record = await mine(session, principal, conversation_id)
    run = run_of(conversation_id)
    after = position_after(last_event_id, after_seq)
    if run is not None:
        return EventSourceResponse(run.follow(after))
    lease = aware(record.turn_lease_until)
    if lease is not None and lease > utcnow():
        raise conflict(
            f"the turn of conversation {conversation_id} runs on another replica: not followable here"
        )
    return EventSourceResponse(_settled(await _latest_answer(session, record), after))


async def _latest_answer(session: AsyncSession, record: AgentSessionRecord) -> AgentResponse:
    """The latest turn as it ended, for a conversation with no turn in flight here: its last answer and
    the signals of the turn (the deltas were not journaled, so the answer comes whole)."""
    said = next(
        (
            str(m["content"])
            for m in reversed(record.messages or [])
            if m.get("role") == "assistant" and m.get("content")
        ),
        "",
    )
    answer = AgentResponse(
        id=f"resp_{uuid4().hex}", session_id=record.id, status="completed", output_text=said
    )
    return await with_signals(session, answer)


async def _settled(answer: AgentResponse, after: int) -> AsyncIterator[dict[str, str]]:
    created = AgentResponse(id=answer.id, session_id=answer.session_id, status="completed")
    events = [
        ("response.created", created.model_dump_json()),
        ("response.completed", answer.model_dump_json()),
    ]
    for position, (name, data) in enumerate(events, start=1):
        if position > after:
            yield {"id": str(position), "event": name, "data": data}


@router.post(
    "/me/conversations/{conversation_id}/cancel",
    response_model=ConversationSummary,
    operation_id="cancelMyConversationTurn",
)
async def cancel_my_conversation_turn(
    conversation_id: str, session: Db, principal: Me
) -> ConversationSummary:
    record = await mine(session, principal, conversation_id)
    if not await cancel_turn(conversation_id, session):
        raise conflict(f"no turn of conversation {conversation_id} is running")
    await session.refresh(record)
    return (await with_assistant(session, [record], [summary_of(record)]))[0]
