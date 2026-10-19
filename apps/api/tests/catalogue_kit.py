"""What the catalogue tests share (S40-09a, ADR 0074)."""

from __future__ import annotations

from httpx import AsyncClient

from .agent_scopes_kit import as_user


async def project_only(client: AsyncClient, email: str) -> None:
    """Sign in as `email` and drop the organisation role the development login gives: what remains is
    a person who belongs to a project only."""
    from choregos_api.db.models import Membership, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import delete, select

    await as_user(client, email)
    async with session_scope() as session:
        user_id = select(User.id).where(User.email == email).scalar_subquery()
        await session.execute(
            delete(Membership).where(Membership.user_id == user_id, Membership.project_id.is_(None))
        )
