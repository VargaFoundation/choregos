"""What the conversation tests share (S63-04): opening, reading and speaking in a conversation."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from sqlalchemy import select

ME = "/api/v1/me/conversations"


async def _open(org_slug: str, email: str, agent: str = "helper", **terms: Any) -> str:
    from choregos_api.db.models import Organization, User
    from choregos_api.db.session import session_scope
    from choregos_api.rbac import Principal
    from choregos_api.services.conversations import open_conversation

    async with session_scope(orgs="*") as session:
        organisation = (
            await session.execute(select(Organization).where(Organization.slug == org_slug))
        ).scalar_one()
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        record = await open_conversation(
            session, Principal(user_id=user.id, email=email), organisation, agent, **terms
        )
        return record.id


async def _record(conversation_id: str) -> Any:
    from choregos_api.db.models import AgentSessionRecord
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        record = await session.get(AgentSessionRecord, conversation_id)
        assert record is not None
        session.expunge(record)
        return record


async def _events(conversation_id: str) -> list[str]:
    from choregos_api.db.models import AgentSessionEvent
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        rows = await session.execute(
            select(AgentSessionEvent.type)
            .where(AgentSessionEvent.session_id == conversation_id)
            .order_by(AgentSessionEvent.seq)
        )
        return list(rows.scalars())


async def _say(client: AsyncClient, conversation_id: str, text: str) -> Any:
    return await client.post(f"{ME}/{conversation_id}/messages", json={"input": text})
