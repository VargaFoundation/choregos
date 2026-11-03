# SPDX-License-Identifier: Apache-2.0
"""Knowledge bases: list, create, read and patch (ADR 0051, ADR 0081)."""

from __future__ import annotations

from functools import partial

from fastapi import status
from sqlalchemy import select

from ...audit import record
from ...db.models import (
    KnowledgeBase,
)
from ...deps import Db, Me, ProjectCtx
from ...errors import conflict, unprocessable
from ...knowledge.access import (
    KnowledgeAction,
    can_manage,
    visible_to,
)
from ...knowledge.conversion import conversions
from ...knowledge.index import reindex_base
from ...rbac import Permission
from ...schemas.knowledge import (
    KnowledgeBaseCreate,
    KnowledgeBaseDto,
    KnowledgeBasePatch,
    ProjectKnowledgeBaseCreate,
)
from ...services.organisations import organisation_by_slug
from ._common import _base, _dto, _grants_of, _known_projects, _manage, _member, _require
from ._router import Slug, router


@router.get("/orgs/{org}/knowledge", response_model=list[KnowledgeBaseDto], operation_id="listKnowledgeBases")
async def list_knowledge_bases(org: str, session: Db, principal: Me) -> list[KnowledgeBaseDto]:
    _member(principal, org)
    organisation = await organisation_by_slug(session, org)
    bases = list(
        (
            await session.execute(
                select(KnowledgeBase)
                .where(KnowledgeBase.org_id == organisation.id)
                .order_by(KnowledgeBase.slug)
            )
        ).scalars()
    )
    # One query for the grants of every base: no per-base lookup (ADR 0081).
    grants = await _grants_of(session, [base.id for base in bases])
    return [
        await _dto(session, principal, org, base, grants=grants[base.id])
        for base in bases
        if visible_to(principal, org, base, grants[base.id])
    ]


@router.post(
    "/orgs/{org}/knowledge",
    response_model=KnowledgeBaseDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="createKnowledgeBase",
)
async def create_knowledge_base(
    org: str, body: KnowledgeBaseCreate, session: Db, principal: Me
) -> KnowledgeBaseDto:
    _manage(principal, org)
    organisation = await organisation_by_slug(session, org)
    if (
        await session.execute(
            select(KnowledgeBase.id).where(
                KnowledgeBase.org_id == organisation.id, KnowledgeBase.slug == body.slug
            )
        )
    ).first():
        raise conflict(f"knowledge base `{body.slug}` already exists")
    await _known_projects(session, organisation, body.projects)
    base = KnowledgeBase(
        org_id=organisation.id,
        slug=body.slug,
        name=body.name,
        description=body.description,
        scope=body.scope,
        projects=sorted(set(body.projects)) if body.scope == "projects" else [],
        groups=sorted(set(body.groups)) if body.scope == "groups" else [],
        serving_mode=body.serving_mode,
        restricted=body.restricted,
        status="active",
        created_by=principal.email,
    )
    session.add(base)
    await session.flush()
    await record(
        session,
        principal,
        "knowledge.create",
        org_id=organisation.id,
        target_type="knowledge_base",
        target_id=base.id,
        scope=base.scope,
    )
    return await _dto(session, principal, org, base, with_documents=True)


@router.post(
    "/projects/{id}/knowledge",
    response_model=KnowledgeBaseDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="createProjectKnowledgeBase",
)
async def create_project_knowledge_base(
    ctx: ProjectCtx, body: ProjectKnowledgeBaseCreate, session: Db
) -> KnowledgeBaseDto:
    """A base owned by this project (S39-14): served to its runs only, curated by its owners
    (`project:write`). Its owners, like the administrator, may widen its reach."""
    ctx.require(Permission.PROJECT_WRITE)
    organisation = await organisation_by_slug(session, ctx.org_slug)
    if (
        await session.execute(
            select(KnowledgeBase.id).where(
                KnowledgeBase.org_id == organisation.id, KnowledgeBase.slug == body.slug
            )
        )
    ).first():
        raise conflict(f"knowledge base `{body.slug}` already exists")
    base = KnowledgeBase(
        org_id=organisation.id,
        slug=body.slug,
        name=body.name,
        description=body.description,
        scope="projects",
        projects=[ctx.project.slug],
        groups=[],
        owner_project=ctx.project.slug,
        serving_mode=body.serving_mode,
        status="active",
        created_by=ctx.principal.email,
    )
    session.add(base)
    await session.flush()
    await record(
        session,
        ctx.principal,
        "knowledge.create",
        org_id=organisation.id,
        target_type="knowledge_base",
        target_id=base.id,
        scope=base.scope,
        owner_project=ctx.project.slug,
    )
    return await _dto(session, ctx.principal, ctx.org_slug, base, with_documents=True)


@router.get("/orgs/{org}/knowledge/{slug}", response_model=KnowledgeBaseDto, operation_id="getKnowledgeBase")
async def get_knowledge_base(org: str, slug: Slug, session: Db, principal: Me) -> KnowledgeBaseDto:
    _member(principal, org)
    return await _dto(
        session, principal, org, await _base(session, principal, org, slug), with_documents=True
    )


@router.patch(
    "/orgs/{org}/knowledge/{slug}", response_model=KnowledgeBaseDto, operation_id="updateKnowledgeBase"
)
async def update_knowledge_base(
    org: str, slug: Slug, body: KnowledgeBasePatch, session: Db, principal: Me
) -> KnowledgeBaseDto:
    base = await _base(session, principal, org, slug)
    changes = body.model_dump(exclude_unset=True)
    widens = any(field in changes for field in ("scope", "projects", "groups"))
    # Who reads and who is served a base is for the administrator and the owners of its project to
    # decide, nobody else (ADR 0081, amended): `restricted` is part of the reach.
    if widens or changes.get("restricted") is not None:
        _require(principal, org, base, KnowledgeAction.WIDEN)
    archives = changes.get("status") is not None
    if archives:
        _require(principal, org, base, KnowledgeAction.ARCHIVE)
    if not (widens or archives) or any(
        changes.get(field) is not None for field in ("name", "description", "serving_mode")
    ):
        _require(principal, org, base, KnowledgeAction.EDIT)
    scope = changes.get("scope", base.scope)
    projects = changes.get("projects", base.projects) or []
    groups = changes.get("groups", base.groups) or []
    if scope == "projects" and not projects:
        raise unprocessable("scope `projects` names at least one project")
    if scope == "groups" and not groups:
        raise unprocessable("scope `groups` names at least one group")
    await _known_projects(
        session, await organisation_by_slug(session, org), projects if scope == "projects" else []
    )
    reindex = changes.get("serving_mode") == "index" and base.serving_mode != "index"
    for field in ("name", "description", "serving_mode", "status", "restricted"):
        if field in changes and changes[field] is not None:
            setattr(base, field, changes[field])
    base.scope = scope
    base.projects = sorted(set(projects)) if scope == "projects" else []
    base.groups = sorted(set(groups)) if scope == "groups" else []
    if widens and can_manage(principal, org):
        # An administrator re-scoped it: it is no longer the owning project's to curate. When the
        # owning project's own owner does it, the base stays theirs (they may narrow it back).
        base.owner_project = None
    await session.flush()
    await record(
        session,
        principal,
        "knowledge.update",
        org_id=base.org_id,
        target_type="knowledge_base",
        target_id=base.id,
        changes=sorted(changes),
    )
    dto = await _dto(session, principal, org, base, with_documents=True)
    if reindex:
        # Switched to `index`: its ready documents are cut and embedded in the background.
        await session.commit()
        conversions.spawn("reindex", partial(reindex_base, org, base.id))
    return dto
