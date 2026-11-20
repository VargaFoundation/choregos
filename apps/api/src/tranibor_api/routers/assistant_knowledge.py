# SPDX-License-Identifier: Apache-2.0
"""Upstream updates proposed to an installation's offered knowledge (S63-06, ADR 0138 point 3).

Owners only (`assistant:manage`, decided by `services.assistant_access`)."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query
from sqlalchemy import select

from ..db.models import KnowledgeUpdateProposal
from ..decision_session import KNOWLEDGE_UPDATES, require_person_session
from ..deps import Config, Db, Me
from ..errors import not_found
from ..schemas.assistant_knowledge import KnowledgeUpdate, KnowledgeUpdateList, UpdateDecision
from ..schemas.assistants import SLUG
from ..services import assistants as service
from ..services import offered_knowledge as knowledge
from ..services.organisations import organisation_by_slug

router = APIRouter(tags=["agents"])

Slug = Annotated[str, Path(pattern=SLUG)]


def _dto(proposal: KnowledgeUpdateProposal, base: str) -> KnowledgeUpdate:
    return KnowledgeUpdate(
        id=proposal.id,
        base=base,
        path=proposal.path,
        kind=proposal.kind,
        diff=proposal.diff,
        proposed_content=proposal.proposed_content,
        plugin_version=proposal.plugin_version,
        status=proposal.status,
        decided_by=proposal.decided_by,
        decided_at=proposal.decided_at,
        created_at=proposal.created_at,
    )


@router.get(
    "/orgs/{org}/assistants/{slug}/knowledge/updates",
    response_model=KnowledgeUpdateList,
    operation_id="listAssistantKnowledgeUpdates",
)
async def list_updates(
    org: str,
    slug: Slug,
    session: Db,
    principal: Me,
    status: Annotated[Literal["proposed", "accepted", "merged", "refused"] | None, Query()] = None,
) -> KnowledgeUpdateList:
    organisation = await organisation_by_slug(session, org)
    row = await service.installation(session, organisation.id, slug)
    service.require_manager(principal, org, row)
    found = await knowledge.proposals(session, row, status)
    return KnowledgeUpdateList(items=[_dto(p, base) for p, base in found])


@router.post(
    "/orgs/{org}/assistants/{slug}/knowledge/updates/{update_id}",
    response_model=KnowledgeUpdate,
    operation_id="decideAssistantKnowledgeUpdate",
)
async def decide_update(
    org: str, slug: Slug, update_id: str, body: UpdateDecision, session: Db, principal: Me, settings: Config
) -> KnowledgeUpdate:
    require_person_session(
        principal, "deciding an assistant's knowledge update", settings=settings, legacy=KNOWLEDGE_UPDATES
    )
    organisation = await organisation_by_slug(session, org)
    row = await service.installation(session, organisation.id, slug)
    service.require_manager(principal, org, row)
    proposal = (
        await session.execute(
            select(KnowledgeUpdateProposal).where(
                KnowledgeUpdateProposal.id == update_id, KnowledgeUpdateProposal.installation_id == row.id
            )
        )
    ).scalar_one_or_none()
    if proposal is None:
        raise not_found("Knowledge update", update_id)
    proposal = await knowledge.decide(session, row, proposal, body, principal)
    base = next(s for p, s in await knowledge.proposals(session, row) if p.id == proposal.id)
    return _dto(proposal, base)
