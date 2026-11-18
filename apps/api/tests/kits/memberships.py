"""One person's membership narrowed to one role on one project, or to nothing."""

from __future__ import annotations


async def _only(email: str, role: str | None = None, project_slug: str | None = None) -> None:
    """The person holds this one role on this one project, or nothing in the organisation (a development
    sign-in makes everyone a developer of it)."""
    from sqlalchemy import delete, select
    from tranibor_api.db.models import Membership, Organization, Project, User
    from tranibor_api.db.session import session_scope

    async with session_scope() as session:
        org = (
            await session.execute(select(Organization).where(Organization.slug == "northwind"))
        ).scalar_one()
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        await session.execute(delete(Membership).where(Membership.user_id == user.id))
        if role is not None:
            project_id = (
                await session.execute(
                    select(Project.id).where(Project.org_id == org.id, Project.slug == project_slug)
                )
            ).scalar_one()
            session.add(Membership(user_id=user.id, org_id=org.id, project_id=project_id, role=role))
