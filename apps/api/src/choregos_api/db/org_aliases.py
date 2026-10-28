# SPDX-License-Identifier: Apache-2.0
"""An organisation's slug is an address, its id is its identity (ADR 0090).

A rename keeps the old slug as an alias; everything that reads a slug from outside the platform (a URL,
an MCP address, an IdP group, a payload that waited in a workflow) resolves it here.

`organizations` is under RLS (a row is visible when its slug is in the scope) and a former slug is, by
definition, not in the scope: resolving it has to read `organizations` under the platform scope. The
helpers below do so for the length of their own query and restore the caller's scope right after, in
the same transaction. They only map an address to an organisation; what the caller may do with it is
still decided by its memberships.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from .models import Organization, OrgSlugAlias
from .session import TOUT, limiter_aux_organisations


@asynccontextmanager
async def _platform_scope(session: AsyncSession) -> AsyncIterator[None]:
    """Read under the platform scope, then put the caller's scope back (PostgreSQL only)."""
    if get_settings().is_sqlite:
        yield
        return
    previous = (
        await session.execute(text("SELECT coalesce(current_setting('app.current_orgs', true), '')"))
    ).scalar_one()
    await limiter_aux_organisations(session, TOUT)
    try:
        yield
    finally:
        await session.execute(text("SELECT set_config('app.current_orgs', :orgs, true)"), {"orgs": previous})


async def current_slug(session: AsyncSession, slug: str) -> str:
    """The organisation's current slug when `slug` is an alias of it, `slug` itself otherwise."""
    return (await current_slugs(session, [slug]))[slug]


async def current_slugs(session: AsyncSession, slugs: Iterable[str]) -> dict[str, str]:
    """`{given slug: current slug}`: an alias maps to its organisation's slug, any other value to itself."""
    wanted = sorted({s for s in slugs if s})
    resolved = {s: s for s in wanted}
    if not wanted:
        return resolved
    async with _platform_scope(session):
        rows = await session.execute(
            select(OrgSlugAlias.alias, Organization.slug)
            .join(Organization, Organization.id == OrgSlugAlias.org_id)
            .where(OrgSlugAlias.alias.in_(wanted))
        )
    for alias, slug in rows.all():
        resolved[alias] = slug
    return resolved


async def organisation_by_slug_or_alias(session: AsyncSession, slug: str) -> Organization | None:
    """The organisation whose current slug, or whose former slug, is `slug`."""
    async with _platform_scope(session):
        found = (
            await session.execute(select(Organization).where(Organization.slug == slug))
        ).scalar_one_or_none()
        if found is not None:
            return found
        return (
            await session.execute(
                select(Organization)
                .join(OrgSlugAlias, OrgSlugAlias.org_id == Organization.id)
                .where(OrgSlugAlias.alias == slug)
            )
        ).scalar_one_or_none()


async def holder_of_slug(session: AsyncSession, slug: str) -> str | None:
    """The id of the organisation that holds `slug`, as its slug or as an alias; `None` when it is free."""
    found = await organisation_by_slug_or_alias(session, slug)
    return found.id if found is not None else None


async def aliases_of(session: AsyncSession, org_id: str) -> list[str]:
    return sorted(
        (await session.execute(select(OrgSlugAlias.alias).where(OrgSlugAlias.org_id == org_id))).scalars()
    )


async def aliases_by_org(session: AsyncSession, org_ids: Iterable[str]) -> dict[str, list[str]]:
    ids = sorted(set(org_ids))
    out: dict[str, list[str]] = {i: [] for i in ids}
    if ids:
        rows = await session.execute(
            select(OrgSlugAlias.org_id, OrgSlugAlias.alias)
            .where(OrgSlugAlias.org_id.in_(ids))
            .order_by(OrgSlugAlias.alias)
        )
        for org_id, alias in rows.all():
            out[org_id].append(alias)
    return out


async def keep_old_slug(session: AsyncSession, org: Organization, old_slug: str, actor: str | None) -> None:
    """Called by a rename: `old_slug` becomes an alias of `org`, and the new slug stops being one."""
    await session.execute(
        delete(OrgSlugAlias).where(OrgSlugAlias.org_id == org.id, OrgSlugAlias.alias == org.slug)
    )
    session.add(OrgSlugAlias(alias=old_slug, org_id=org.id, created_by=actor))
    await session.flush()


async def release(session: AsyncSession, org_id: str, alias: str) -> bool:
    """Drop one alias of the organisation; `False` when it had none by that name."""
    result = await session.execute(
        delete(OrgSlugAlias).where(OrgSlugAlias.org_id == org_id, OrgSlugAlias.alias == alias)
    )
    return bool(getattr(result, "rowcount", 0))
