# SPDX-License-Identifier: Apache-2.0
"""A work item whose runs read a restricted knowledge base is marked, and read only by its readers
(ADR 0114, S42-11f).

`pin` serves a restricted base only on behalf of a person who reads it (ADR 0097); what the run then
writes lands under the work item. The work item records the base's slug in `restricted_knowledge`
(only ever added to), and every route that serves the work item or anything under it (its runs, their
transcript, logs and diff, its artefacts, receipts, timeline, journey, pending question) asks
`readable` first: the caller must read every base it names, by the console's rule (`visible_to`),
evaluated against the bases as they are now. A base that no longer exists keeps the work item closed
to everyone but administrators.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import KnowledgeBase, KnowledgeGrant, Organization, WorkItem
from ..errors import not_found
from ..rbac import Principal
from ..schemas import WorkItemDto
from .access import can_manage, visible_to


def mark(item: WorkItem, slugs: Iterable[str]) -> bool:
    """Adds these bases to the work item's mark; whether anything was added."""
    before = set(item.restricted_knowledge or [])
    after = before | set(slugs)
    if after == before:
        return False
    item.restricted_knowledge = sorted(after)
    return True


async def unread_bases(
    session: AsyncSession, principal: Principal, org_id: str, org_slug: str, item: WorkItem
) -> list[str]:
    """The bases of the work item's mark this caller does not read (empty: the caller reads it)."""
    slugs = list(item.restricted_knowledge or [])
    if not slugs or can_manage(principal, org_slug):
        return []
    bases = {
        base.slug: base
        for base in (
            await session.execute(
                select(KnowledgeBase).where(KnowledgeBase.org_id == org_id, KnowledgeBase.slug.in_(slugs))
            )
        ).scalars()
    }
    grants: dict[str, list[Any]] = {base.id: [] for base in bases.values()}
    if grants:
        for grant in (
            await session.execute(select(KnowledgeGrant).where(KnowledgeGrant.knowledge_base_id.in_(grants)))
        ).scalars():
            grants[grant.knowledge_base_id].append(grant)
    return [
        slug
        for slug in slugs
        if slug not in bases or not visible_to(principal, org_slug, bases[slug], grants[bases[slug].id])
    ]


async def readable(session: AsyncSession, principal: Principal, item: WorkItem, org_id: str) -> bool:
    """Whether this caller reads the content of the work item (its project's role is checked apart)."""
    if not item.restricted_knowledge:
        return True
    organisation = await session.get(Organization, org_id)
    org_slug = organisation.slug if organisation is not None else ""
    return not await unread_bases(session, principal, org_id, org_slug, item)


async def require_readable(session: AsyncSession, principal: Principal, item: WorkItem, org_id: str) -> None:
    """404 for a caller who does not read every base of the mark: the work item's content does not
    exist for them, as a restricted base does not exist for a person without a grant."""
    if not await readable(session, principal, item, org_id):
        raise not_found("Work item", item.id)


#: What a list shows of a marked work item to a caller who does not read it (ADR 0114, point B).
LISTED = frozenset(
    {
        "id",
        "project_slug",
        "tracker_key",
        "title",
        "state",
        "state_display",
        "workflow_name",
        "workflow_version",
        "url",
        "created_at",
        "closed_at",
        "restricted_knowledge",
    }
)


def listed_only(dto: WorkItemDto) -> WorkItemDto:
    """The list entry of a work item the caller may not read: key, title, state, dates, the mark."""
    return WorkItemDto(**{name: getattr(dto, name) for name in LISTED}, redacted=True)
