# SPDX-License-Identifier: Apache-2.0
"""The one organisation lookup of the routers (ADR 0099)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Organization
from ..errors import not_found


async def organisation_by_slug(session: AsyncSession, slug: str) -> Organization:
    """The organisation with this slug, or the 404 `not_found('Organisation', slug)`.

    Callers that must check membership or a role first do it before calling, because row-level
    security hides the row otherwise. This does NOT resolve former slugs: OrgAliasMiddleware
    (ADR 0090) rewrites `/api/v1/orgs/<alias>/...` to the current slug before routing, and slugs
    from payloads or signed states are resolved by the callers through `db.org_aliases.current_slug`
    or `organisation_by_slug_or_alias`. Do not turn this into an alias lookup.
    """
    organisation = (
        await session.execute(select(Organization).where(Organization.slug == slug))
    ).scalar_one_or_none()
    if organisation is None:
        raise not_found("Organisation", slug)
    return organisation
