# SPDX-License-Identifier: Apache-2.0
"""The caller's assistants and the opening of their conversations (S63-15, ADR 0133 point 5).

`GET /me/assistants` lists the installations whose audience holds the caller; `POST
/me/assistants/{org}/{slug}/conversations` opens a conversation of a `ready` one in the caller's personal
space. Anyone outside the audience is answered 404 on both: the assistant does not exist for them.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, status

from ..deps import Db, Me
from ..schemas.agent_sessions import Conversation
from ..schemas.assistants import SLUG, AssistantConversationOpen, MyAssistantList
from ..services.assistant_conversations import my_assistants, open_assistant_conversation
from ..services.conversations import conversation_of

router = APIRouter(tags=["agents"])

Slug = Annotated[str, Path(pattern=SLUG)]


@router.get("/me/assistants", response_model=MyAssistantList, operation_id="listMyAssistants")
async def list_my_assistants(session: Db, principal: Me) -> MyAssistantList:
    return MyAssistantList(items=await my_assistants(session, principal))


@router.post(
    "/me/assistants/{org}/{slug}/conversations",
    response_model=Conversation,
    status_code=status.HTTP_201_CREATED,
    operation_id="openMyAssistantConversation",
)
async def open_my_assistant_conversation(
    org: str, slug: Slug, body: AssistantConversationOpen, session: Db, principal: Me
) -> Conversation:
    record = await open_assistant_conversation(session, principal, org, slug, body.context)
    return conversation_of(record)
