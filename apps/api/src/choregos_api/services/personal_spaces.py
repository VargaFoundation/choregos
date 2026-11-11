# SPDX-License-Identifier: Apache-2.0
"""A person's personal space (ADR 0134, S63-03).

A personal space is a project of kind `personal`, one per person and organisation, created the first time
the person needs one (their first conversation with an assistant), never by hand. It reuses what a project
carries — the cost ledger, budgets, work items for tasks, memory — and is:

- **left out of every project listing**: the console's, the MCP door's and the A2A door's (`listed`);
- **read by its owner only**: a RESTRICTIVE row-level policy on `projects` lets the platform scope and the
  owner (`app.current_user`) read it, whatever roles another person holds in the organisation; tables keyed
  on a project follow, because their policies resolve the project through `projects` under RLS;
- **named by a reserved slug**, `personal-<12 hex of the user id>`, which no team project may take.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import ColumnElement, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Organization, Project, User

PERSONAL = "personal"
STANDARD = "standard"
#: The slugs of personal spaces; a team project's slug may not start with it.
PERSONAL_PREFIX = "personal-"


def personal_slug(user_id: str) -> str:
    """The reserved slug of a person's space: stable, derived from the user id, never their address."""
    return PERSONAL_PREFIX + hashlib.sha256(user_id.encode()).hexdigest()[:12]


def is_reserved_slug(slug: str) -> bool:
    """A team project may not take a personal space's slug."""
    return slug.startswith(PERSONAL_PREFIX)


def listed() -> ColumnElement[bool]:
    """The predicate of a project listing: personal spaces are never listed."""
    return Project.kind != PERSONAL


def is_personal(project: Project) -> bool:
    return project.kind == PERSONAL


async def find_personal_space(
    session: AsyncSession, organisation: Organization, user: User
) -> Project | None:
    """The person's space in the organisation, if it exists."""
    return (
        await session.execute(
            select(Project).where(
                Project.org_id == organisation.id, Project.kind == PERSONAL, Project.owner_user_id == user.id
            )
        )
    ).scalar_one_or_none()


async def personal_space(session: AsyncSession, organisation: Organization, user: User) -> Project:
    """The person's space in the organisation, created on first use. Idempotent, and safe when two requests
    of the same person race: the unique index keeps one, and the loser reads it back."""
    existing = await find_personal_space(session, organisation, user)
    if existing is not None:
        return existing
    space = Project(
        org_id=organisation.id,
        slug=personal_slug(user.id),
        name=f"Personal space of {user.display_name or user.email}",
        # A project with no repository and no connector: what a person's tasks need comes later (ADR 0137).
        config={"org": organisation.slug, "slug": personal_slug(user.id)},
        status="active",
        kind=PERSONAL,
        owner_user_id=user.id,
    )
    try:
        async with session.begin_nested():
            session.add(space)
            await session.flush()
    except IntegrityError:
        raced = await find_personal_space(session, organisation, user)
        if raced is None:
            raise
        return raced
    return space
