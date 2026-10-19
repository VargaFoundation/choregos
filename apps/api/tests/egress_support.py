"""Shared helpers of the egress tests (S39-22): members, enforcement, a client per person."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from httpx import ASGITransport, AsyncClient

from .conftest import login

ORG = "/api/v1/orgs/varga"


async def add_member(email: str, role: str) -> None:
    from choregos_api.db.models import Membership, Organization, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        org = (await session.execute(select(Organization).where(Organization.slug == "varga"))).scalar_one()
        user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            user = User(email=email, display_name=email.split("@", maxsplit=1)[0])
            session.add(user)
            await session.flush()
        session.add(Membership(user_id=user.id, org_id=org.id, role=role))


@asynccontextmanager
async def as_person(client: AsyncClient, email: str, role: str | None = None) -> AsyncIterator[AsyncClient]:
    """Another signed-in person on the same application."""
    if role:
        await add_member(email, role)
    async with AsyncClient(
        transport=ASGITransport(app=client._transport.app),  # type: ignore[attr-defined]
        base_url="http://test",
    ) as other:
        await login(other, email)
        yield other


async def rules_in_db(status: str | None = None) -> list[Any]:
    from choregos_api.db.models import OrgEgressRule
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        query = select(OrgEgressRule).order_by(OrgEgressRule.host)
        if status:
            query = query.where(OrgEgressRule.status == status)
        return list((await session.execute(query)).scalars())


async def audit_actions() -> list[str]:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (await session.execute(select(AuditLog.action).order_by(AuditLog.ts))).tuples()
        return [a for (a,) in rows if a.startswith("egress.")]
