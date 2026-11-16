# SPDX-License-Identifier: Apache-2.0
"""Whose a conversation is: the one rule the conversation routes and a followed turn share (ADR 0134)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AgentSessionRecord, Organization
from ..errors import not_found
from ..rbac import Principal


async def mine(session: AsyncSession, principal: Principal, conversation_id: str) -> AgentSessionRecord:
    """The caller's own conversation, in an organisation they still belong to; 404 otherwise."""
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
