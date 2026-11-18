# SPDX-License-Identifier: Apache-2.0
"""The grants on a knowledge base: who holds which role (ADR 0081)."""

from __future__ import annotations

from ...audit import record
from ...db.models import (
    KnowledgeGrant,
)
from ...deps import Db, Me
from ...knowledge.access import (
    KnowledgeAction,
)
from ...schemas.knowledge import (
    KnowledgeGrants,
    KnowledgeGrantsPut,
)
from ...services.organisations import organisation_by_slug
from ._common import _GRANTS, _base, _checked_grants, _grant_dtos, _load_grants, _member, _require
from ._router import Slug, router


@router.get(
    "/orgs/{org}/knowledge/{slug}/grants",
    response_model=KnowledgeGrants,
    operation_id="listKnowledgeGrants",
)
async def list_knowledge_grants(org: str, slug: Slug, session: Db, principal: Me) -> KnowledgeGrants:
    """Who holds a role on the base. Shown to whoever may change it only."""
    _member(principal, org)
    base = await _base(session, principal, org, slug)
    _require(principal, org, base, KnowledgeAction.SHARE)
    return KnowledgeGrants(grants=await _grant_dtos(session, base, await _load_grants(session, base)))


@router.put(
    "/orgs/{org}/knowledge/{slug}/grants",
    response_model=KnowledgeGrants,
    operation_id="putKnowledgeGrants",
)
async def put_knowledge_grants(
    org: str, slug: Slug, body: KnowledgeGrantsPut, session: Db, principal: Me
) -> KnowledgeGrants:
    """Replaces the list of grants: the same call twice changes nothing. A grant gives people a role;
    it never serves a run."""
    _member(principal, org)
    base = await _base(session, principal, org, slug)
    _require(principal, org, base, KnowledgeAction.SHARE)
    organisation = await organisation_by_slug(session, org)
    wanted = await _checked_grants(session, organisation, body.grants)
    current = {(grant.subject_type, grant.subject): grant for grant in await _load_grants(session, base)}
    added = sorted(
        f"{kind}:{subject}={role}"
        for (kind, subject), role in wanted.items()
        if (kind, subject) not in current
    )
    removed = sorted(f"{kind}:{subject}" for (kind, subject) in current if (kind, subject) not in wanted)
    changed = sorted(
        f"{kind}:{subject}={role}"
        for (kind, subject), role in wanted.items()
        if (kind, subject) in current and current[(kind, subject)].role != role
    )
    for key, grant in current.items():
        if key not in wanted:
            await session.delete(grant)
        elif grant.role != wanted[key]:
            grant.role = wanted[key]
    for (kind, subject), role in wanted.items():
        if (kind, subject) not in current:
            session.add(
                KnowledgeGrant(
                    org_id=base.org_id,
                    knowledge_base_id=base.id,
                    subject_type=kind,
                    subject=subject,
                    role=role,
                    created_by=principal.email,
                )
            )
    await session.flush()
    if added or removed or changed:
        await record(
            session,
            principal,
            "knowledge.grant.set",
            org_id=base.org_id,
            target_type="knowledge_base",
            target_id=base.id,
            added=added,
            removed=removed,
            changed=changed,
        )
    _GRANTS.pop(base, None)
    return KnowledgeGrants(grants=await _grant_dtos(session, base, await _load_grants(session, base)))
