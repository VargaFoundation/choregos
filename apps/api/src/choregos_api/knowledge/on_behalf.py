# SPDX-License-Identifier: Apache-2.0
"""On whose behalf a run reads a restricted knowledge base (ADR 0097, S42-11e).

A run acts for the person who started its work item (`initiated_by`, ADR 0077 §2). A fan-out child
(`work_item:<key>`) acts for its parent's initiator, followed a few levels within the project. Every
other kind names no person, and a run with no person behind it is never served a restricted base.

The person's roles are rebuilt from the database, in the run's organisation only, without touching
the session's row-level-security scope (`deps._principal_from_user` widens it; this does not).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from choregos_contracts import Role
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import KnowledgeGrant, Membership, Organization, Project, User, WorkItem
from ..rbac import Principal
from .access import served_to_project, visible_to

#: The initiator kinds whose subject is a person's e-mail.
PERSON_KINDS = frozenset({"person", "mcp", "trial"})
#: How many fan-out parents are followed before giving up.
MAX_PARENTS = 5


async def person_of(session: AsyncSession, project_id: str, initiated_by: str | None) -> str | None:
    """The e-mail of the person a run acts for, or None when nobody stands behind it."""
    for _ in range(MAX_PARENTS + 1):
        kind, _, subject = (initiated_by or "").partition(":")
        if kind in PERSON_KINDS:
            return subject or None
        if kind != "work_item" or not subject:
            return None
        initiated_by = (
            await session.execute(
                select(WorkItem.initiated_by).where(
                    WorkItem.project_id == project_id, WorkItem.tracker_key == subject
                )
            )
        ).scalar_one_or_none()
    return None


async def principal_in_org(session: AsyncSession, org_id: str, email: str) -> Principal | None:
    """The person's roles and groups in one organisation, or None when they have no account."""
    user = (
        await session.execute(select(User).where(func.lower(User.email) == email.lower()))
    ).scalar_one_or_none()
    if user is None:
        return None
    rows = (
        await session.execute(
            select(Membership.role, Organization.slug, Project.slug)
            .join(Organization, Membership.org_id == Organization.id)
            .outerjoin(Project, Membership.project_id == Project.id)
            .where(Membership.user_id == user.id, Membership.org_id == org_id)
        )
    ).all()
    org_roles: dict[str, Role] = {}
    project_roles: dict[str, Role] = {}
    for role, org_slug, project_slug in rows:
        if project_slug:
            project_roles[f"{org_slug}/{project_slug}"] = Role(role)
        else:
            org_roles[org_slug] = Role(role)
    return Principal(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        kind="user",
        org_roles=org_roles,
        project_roles=project_roles,
        groups=[str(g) for g in (user.groups or [])],
    )


async def _grants(session: AsyncSession, base_id: str) -> list[KnowledgeGrant]:
    return list(
        (
            await session.execute(select(KnowledgeGrant).where(KnowledgeGrant.knowledge_base_id == base_id))
        ).scalars()
    )


class OnBehalf:
    """Answers "is this base served to this run" for one run, resolving its person once."""

    def __init__(self, session: AsyncSession, org_id: str, project_id: str, initiated_by: str | None):
        self._session, self._org_id, self._project_id = session, org_id, project_id
        self._initiated_by = initiated_by
        self._resolved = False
        self.email: str | None = None
        self._principal: Principal | None = None
        self._org_slug = ""

    async def _resolve(self) -> None:
        if self._resolved:
            return
        self._resolved = True
        self.email = await person_of(self._session, self._project_id, self._initiated_by)
        if self.email:
            self._principal = await principal_in_org(self._session, self._org_id, self.email)
        organisation = await self._session.get(Organization, self._org_id)
        self._org_slug = organisation.slug if organisation is not None else ""

    async def refusal(self, base: Any, project_slug: str) -> str | None:
        """None when the run is served the base, otherwise the sentence that says why not."""
        if not served_to_project(base, project_slug):
            return (
                f"the knowledge base `{base.slug}` is not served to the project `{project_slug}` "
                f"(scope `{base.scope}`, status `{base.status}`)"
            )
        if not getattr(base, "restricted", False):
            return None
        await self._resolve()
        if self.email is None:
            return (
                f"the knowledge base `{base.slug}` is restricted and the run acts for nobody "
                f"(started by `{self._initiated_by or 'unknown'}`); start the work item as a person "
                "who reads the base"
            )
        grants: Iterable[Any] = await _grants(self._session, base.id)
        if self._principal is None or not visible_to(self._principal, self._org_slug, base, grants):
            return f"the knowledge base `{base.slug}` is restricted and `{self.email}` may not read it"
        return None
