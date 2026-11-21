"""Members of one project only, and a second signed-in client, for the budget-alert tests (ADR 0206)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from httpx import ASGITransport, AsyncClient

from ..conftest import login


async def _member(email: str, role: str, project_slug: str | None = None) -> None:
    from sqlalchemy import select
    from tranibor_api.db.models import Membership, Organization, Project, User
    from tranibor_api.db.session import session_scope

    async with session_scope() as session:
        org = (
            await session.execute(select(Organization).where(Organization.slug == "northwind"))
        ).scalar_one()
        user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            user = User(email=email, display_name=email.split("@", maxsplit=1)[0])
            session.add(user)
            await session.flush()
        project_id = None
        if project_slug:
            project_id = (
                await session.execute(
                    select(Project.id).where(Project.org_id == org.id, Project.slug == project_slug)
                )
            ).scalar_one()
        session.add(Membership(user_id=user.id, org_id=org.id, project_id=project_id, role=role))


@asynccontextmanager
async def _as(client: AsyncClient, email: str) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=client._transport.app), base_url="http://test") as c:  # type: ignore[attr-defined]
        await login(c, email)
        yield c


async def _drop_organisation_role(email: str) -> None:
    """The development sign-in grants a role on the organisation; a production member of one project
    only has none, so it is taken back after the sign-in."""
    from sqlalchemy import delete, select
    from tranibor_api.db.models import Membership, User
    from tranibor_api.db.session import session_scope

    async with session_scope() as session:
        user_id = (await session.execute(select(User.id).where(User.email == email))).scalar_one()
        await session.execute(
            delete(Membership).where(Membership.user_id == user_id, Membership.project_id.is_(None))
        )
