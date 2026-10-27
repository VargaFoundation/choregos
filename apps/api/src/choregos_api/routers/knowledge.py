# SPDX-License-Identifier: Apache-2.0
"""Knowledge bases of the organisation (ADR 0051).

A knowledge base is a curated corpus a human chose; its documents are uploaded here, answered 202
`pending`, and converted to markdown in the background (`knowledge.conversion`), never inside the
request. Curating is the organisation administrator's (`agent:manage`), or the owners' of the project that
owns the base (`project:write`, S39-14), who may also widen or narrow its reach (ADR 0081, amended).
Reading follows the base's scope (`knowledge.access`).
"""

from __future__ import annotations

import contextlib
import hashlib
import re
from collections.abc import Callable
from dataclasses import asdict
from functools import partial
from typing import Annotated, Any
from weakref import WeakKeyDictionary

import httpx
from choregos_adapters.registry import fakes_enabled
from choregos_core.outbound_proxy import OutboundProxyError
from fastapi import APIRouter, Path, Query, Request, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer
from starlette.datastructures import UploadFile

from ..audit import record
from ..config import get_settings
from ..db.models import (
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeGrant,
    KnowledgeSource,
    Membership,
    Organization,
    OrgConnector,
    Project,
    User,
)
from ..deps import Db, Me, ProjectCtx
from ..errors import ApiError, conflict, forbidden, not_found, unprocessable
from ..knowledge import refresh
from ..knowledge.access import (
    ROLES,
    KnowledgeAction,
    can_manage,
    decide,
    role_of,
    visible_to,
)
from ..knowledge.conversion import PENDING, conversions
from ..knowledge.formats import DocumentRefusedError, accept
from ..knowledge.index import evaluate, reindex_base, search, search_mode
from ..knowledge.sync import CONNECTED_KINDS, OPTIONALLY_CONNECTED, SOURCE_CONNECTOR_TYPES
from ..logging import get_logger
from ..rbac import Permission
from ..schemas.knowledge import (
    KnowledgeBaseCreate,
    KnowledgeBaseDto,
    KnowledgeBasePatch,
    KnowledgeDistillEstimate,
    KnowledgeDistillRequest,
    KnowledgeDocumentDto,
    KnowledgeEvalSet,
    KnowledgeGrantDto,
    KnowledgeGrants,
    KnowledgeGrantsPut,
    KnowledgePermissions,
    KnowledgeSearchRequest,
    KnowledgeSearchResult,
    KnowledgeSourceCreate,
    KnowledgeSourceDto,
    KnowledgeSourcePatch,
    ProjectKnowledgeBaseCreate,
)
from ..services.egress import egress_scope, ensure_or_409, knowledge_source_targets, targets_of_connector
from ..services.outbound_proxy import proxy_scope
from ..services.platform_network import check_platform_host
from ..temporal import get_temporal, knowledge_distill_id, knowledge_sync_id

log = get_logger("choregos.knowledge.router")

router = APIRouter(tags=["knowledge"])

Slug = Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]{1,62}$")]
#: What a multipart envelope adds around the file, at most, before the size cap refuses it.
ENVELOPE = 64 * 1024


async def _organisation(session: AsyncSession, slug: str) -> Organization:
    organisation = (
        await session.execute(select(Organization).where(Organization.slug == slug))
    ).scalar_one_or_none()
    if organisation is None:
        raise not_found("Organisation", slug)
    return organisation


def _member(principal: Me, org: str) -> None:
    if principal.org_roles.get(org) is None and not any(
        key.startswith(f"{org}/") for key in principal.project_roles
    ):
        raise forbidden(f"reading the knowledge bases of {org} requires being a member of it")


def _manage(principal: Me, org: str) -> None:
    if not can_manage(principal, org):
        raise forbidden("curating a knowledge base needs the org_admin role")


#: The grants of the bases loaded in this request, so `_require` never queries twice for one base.
_GRANTS: WeakKeyDictionary[KnowledgeBase, list[KnowledgeGrant]] = WeakKeyDictionary()


def _require(principal: Me, org: str, base: KnowledgeBase, action: KnowledgeAction) -> None:
    """The one gate (ADR 0081): the server's own sentence when the caller may not."""
    decision = decide(principal, org, base, action, _GRANTS.get(base, []))
    if not decision.allowed:
        raise forbidden(decision.reason or "not allowed on this knowledge base")


async def _grants_of(session: AsyncSession, base_ids: list[str]) -> dict[str, list[KnowledgeGrant]]:
    """The grants of these bases, in one query."""
    found: dict[str, list[KnowledgeGrant]] = {base_id: [] for base_id in base_ids}
    if not base_ids:
        return found
    rows = (
        await session.execute(
            select(KnowledgeGrant)
            .where(KnowledgeGrant.knowledge_base_id.in_(base_ids))
            .order_by(KnowledgeGrant.subject_type, KnowledgeGrant.subject)
        )
    ).scalars()
    for grant in rows:
        found[grant.knowledge_base_id].append(grant)
    return found


async def _load_grants(session: AsyncSession, base: KnowledgeBase) -> list[KnowledgeGrant]:
    if base not in _GRANTS:
        _GRANTS[base] = (await _grants_of(session, [base.id]))[base.id]
    return _GRANTS[base]


async def _base(session: AsyncSession, principal: Me, org: str, slug: str) -> KnowledgeBase:
    """The base, if the caller may read it; one it may not read does not exist for it (404).

    Reading is the reach, or any role granted on the base (ADR 0081), so the grants are loaded here.
    """
    organisation = await _organisation(session, org)
    base = (
        await session.execute(
            select(KnowledgeBase).where(KnowledgeBase.org_id == organisation.id, KnowledgeBase.slug == slug)
        )
    ).scalar_one_or_none()
    if base is None:
        raise not_found("Knowledge base", f"{org}/{slug}")
    grants = await _load_grants(session, base)
    if not decide(principal, org, base, KnowledgeAction.READ, grants).allowed:
        raise not_found("Knowledge base", f"{org}/{slug}")
    return base


async def _known_projects(session: AsyncSession, organisation: Organization, slugs: list[str]) -> None:
    if not slugs:
        return
    known = set(
        (
            await session.execute(
                select(Project.slug).where(Project.org_id == organisation.id, Project.slug.in_(slugs))
            )
        ).scalars()
    )
    if unknown := sorted(set(slugs) - known):
        raise unprocessable(f"unknown project(s) in {organisation.slug}: {', '.join(unknown)}")


def _document_dto(document: KnowledgeDocument, *, with_markdown: bool = False) -> KnowledgeDocumentDto:
    return KnowledgeDocumentDto(
        id=document.id,
        filename=document.filename,
        title=document.title,
        mime=document.mime,
        size_bytes=document.size_bytes,
        digest=document.digest,
        source=document.source,
        status=document.status,
        error=document.error,
        warnings=list(document.warnings or []),
        created_by=document.created_by,
        created_at=document.created_at,
        converted_at=document.converted_at,
        markdown=document.markdown if with_markdown else None,
        source_id=document.source_id,
        source_url=document.source_url,
    )


async def _dto(
    session: AsyncSession,
    principal: Me,
    org: str,
    base: KnowledgeBase,
    *,
    with_documents: bool = False,
    grants: list[KnowledgeGrant] | None = None,
) -> KnowledgeBaseDto:
    held = grants if grants is not None else await _load_grants(session, base)
    allowed = {action: decide(principal, org, base, action, held) for action in KnowledgeAction}
    can = KnowledgePermissions(
        edit=allowed[KnowledgeAction.EDIT].allowed,
        distill=allowed[KnowledgeAction.DISTILL].allowed,
        share=allowed[KnowledgeAction.SHARE].allowed,
        archive=allowed[KnowledgeAction.ARCHIVE].allowed,
        widen=allowed[KnowledgeAction.WIDEN].allowed,
    )
    shown = await _grant_dtos(session, base, held) if with_documents and can.share else None
    counts = {
        str(state): int(count)
        for state, count in (
            await session.execute(
                select(KnowledgeDocument.status, func.count())
                .where(KnowledgeDocument.knowledge_base_id == base.id)
                .group_by(KnowledgeDocument.status)
            )
        ).tuples()
    }
    documents = None
    if with_documents:
        documents = [
            _document_dto(document)
            for document in (
                await session.execute(
                    select(KnowledgeDocument)
                    .where(KnowledgeDocument.knowledge_base_id == base.id)
                    .order_by(KnowledgeDocument.created_at.desc(), KnowledgeDocument.id.desc())
                )
            ).scalars()
        ]
    return KnowledgeBaseDto(
        slug=base.slug,
        name=base.name,
        description=base.description,
        scope=base.scope,
        projects=list(base.projects or []),
        groups=list(base.groups or []),
        serving_mode=base.serving_mode,
        status=base.status,
        document_count=sum(counts.values()),
        documents_by_status=counts,
        created_by=base.created_by,
        created_at=base.created_at,
        updated_at=base.updated_at,
        documents=documents,
        index=await _index_state(session, base) if base.serving_mode == "index" else None,
        eval_set=list(base.eval_set or []),
        eval_result=base.eval_result,
        sources=[await _source_dto(session, source) for source in await _sources(session, base)]
        if with_documents
        else None,
        distillation=base.distillation,
        owner_project=base.owner_project,
        restricted=base.restricted,
        can=can,
        cannot_reason=allowed[KnowledgeAction.EDIT].reason,
        my_role=role_of(principal, org, base, held),
        grants=shown,
    )


async def _sources(session: AsyncSession, base: KnowledgeBase) -> list[KnowledgeSource]:
    return list(
        (
            await session.execute(
                select(KnowledgeSource)
                .where(KnowledgeSource.knowledge_base_id == base.id)
                .order_by(KnowledgeSource.created_at, KnowledgeSource.id)
            )
        ).scalars()
    )


async def _source_dto(session: AsyncSession, source: KnowledgeSource) -> KnowledgeSourceDto:
    count = (
        await session.execute(select(func.count()).where(KnowledgeDocument.source_id == source.id))
    ).scalar_one()
    return KnowledgeSourceDto(
        id=source.id,
        kind=source.kind,
        name=source.name,
        connector=source.connector,
        config=dict(source.config or {}),
        interval_minutes=source.interval_minutes,
        refresh_mode=source.refresh_mode,
        refresh_cron=source.refresh_cron,
        refresh_timezone=source.refresh_timezone,
        next_sync_at=source.next_sync_at,
        sync_requested_at=source.sync_requested_at,
        status=source.status,
        last_synced_at=source.last_synced_at,
        last_error=source.last_error,
        last_summary=source.last_summary,
        document_count=int(count),
    )


async def _index_state(session: AsyncSession, base: KnowledgeBase) -> dict[str, object]:
    chunks, documents = (
        await session.execute(
            select(
                func.count(KnowledgeChunk.id), func.count(func.distinct(KnowledgeChunk.document_id))
            ).where(KnowledgeChunk.knowledge_base_id == base.id)
        )
    ).one()
    return {"chunks": int(chunks), "documents": int(documents), "mode": await search_mode(session)}


@router.get("/orgs/{org}/knowledge", response_model=list[KnowledgeBaseDto], operation_id="listKnowledgeBases")
async def list_knowledge_bases(org: str, session: Db, principal: Me) -> list[KnowledgeBaseDto]:
    _member(principal, org)
    organisation = await _organisation(session, org)
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
    organisation = await _organisation(session, org)
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
    organisation = await _organisation(session, ctx.org_slug)
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
    await _known_projects(session, await _organisation(session, org), projects if scope == "projects" else [])
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


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


async def _members_of(session: AsyncSession, organisation: Organization) -> list[User]:
    """The people who belong to the organisation, at any level."""
    return list(
        (
            await session.execute(
                select(User)
                .join(Membership, Membership.user_id == User.id)
                .where(Membership.org_id == organisation.id)
                .distinct()
            )
        ).scalars()
    )


async def _grant_dtos(
    session: AsyncSession, base: KnowledgeBase, grants: list[KnowledgeGrant]
) -> list[KnowledgeGrantDto]:
    """The grants as shown: a name for each user, and which e-mails still wait for a person."""
    organisation = await session.get(Organization, base.org_id)
    members = await _members_of(session, organisation) if organisation is not None else []
    by_id = {member.id: member for member in members}
    by_email = {member.email.lower(): member for member in members}
    shown: list[KnowledgeGrantDto] = []
    for grant in grants:
        display: str | None = grant.subject
        pending = False
        if grant.subject_type == "user":
            member = by_id.get(grant.subject) or by_email.get(grant.subject.lower())
            pending = member is None
            display = (member.display_name or member.email) if member is not None else grant.subject
        shown.append(
            KnowledgeGrantDto(
                subject_type=grant.subject_type,
                subject=grant.subject,
                display=display,
                role=grant.role,
                pending=pending,
                created_by=grant.created_by,
                created_at=grant.created_at,
            )
        )
    return shown


async def _checked_grants(
    session: AsyncSession, organisation: Organization, wanted: list[KnowledgeGrantDto]
) -> dict[tuple[str, str], str]:
    """The (type, subject) -> role map to store, or a 422 saying what is wrong.

    A user is a member id, or an e-mail: the id of the member that owns it when there is one, else the
    e-mail as typed in lower case, which matches that person the day they sign in (D11-4). A project
    must exist; a group is a free IdP name.
    """
    members = await _members_of(session, organisation)
    by_id = {member.id: member for member in members}
    by_email = {member.email.lower(): member for member in members}
    known = set(
        (await session.execute(select(Project.slug).where(Project.org_id == organisation.id))).scalars()
    )
    stored: dict[tuple[str, str], str] = {}
    for item in wanted:
        subject = item.subject.strip()
        if item.subject_type == "user":
            if "@" in subject:
                if not _EMAIL.match(subject):
                    raise unprocessable(f"`{subject}` is not an e-mail address")
                member = by_email.get(subject.lower())
                subject = member.id if member is not None else subject.lower()
            elif subject not in by_id:
                raise unprocessable(f"unknown member in {organisation.slug}: {subject}")
        elif item.subject_type == "project":
            if subject not in known:
                raise unprocessable(f"unknown project(s) in {organisation.slug}: {subject}")
        elif not subject:
            raise unprocessable("a group grant names an IdP group")
        if item.role not in ROLES:
            raise unprocessable(f"unknown role `{item.role}`")
        if (item.subject_type, subject) in stored:
            raise unprocessable(f"{item.subject_type} `{subject}` is listed twice")
        stored[(item.subject_type, subject)] = item.role
    return stored


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
    organisation = await _organisation(session, org)
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


@router.post(
    "/orgs/{org}/knowledge/{slug}/documents",
    response_model=KnowledgeDocumentDto,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="uploadKnowledgeDocument",
)
async def upload_knowledge_document(
    org: str, slug: Slug, request: Request, response: Response, session: Db, principal: Me
) -> KnowledgeDocumentDto:
    """One file, multipart (`file`). Answered `pending`; the conversion runs in the background.

    The same bytes twice are one document: the second upload answers the first (200).
    """
    base = await _base(session, principal, org, slug)
    _require(principal, org, base, KnowledgeAction.EDIT)
    settings = get_settings()
    cap = settings.knowledge_max_upload_mib * 1024 * 1024
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cap + ENVELOPE:
        raise _too_large(settings.knowledge_max_upload_mib)
    if base.status != "active":
        raise conflict(f"knowledge base `{slug}` is archived")
    async with request.form(max_files=1, max_fields=2) as form:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise unprocessable("expected one file in the multipart field `file`")
        data = await upload.read(cap + 1)
        filename = upload.filename or ""
    if len(data) > cap:
        raise _too_large(settings.knowledge_max_upload_mib)
    try:
        accepted = accept(filename, data)
    except DocumentRefusedError as refusal:
        raise unprocessable(str(refusal)) from refusal
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    existing = (
        await session.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.knowledge_base_id == base.id, KnowledgeDocument.digest == digest
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return _document_dto(existing)
    count = (
        await session.execute(select(func.count()).where(KnowledgeDocument.knowledge_base_id == base.id))
    ).scalar_one()
    if count >= settings.knowledge_max_documents:
        raise conflict(f"knowledge base `{slug}` already holds {count} documents, its maximum")
    document = KnowledgeDocument(
        org_id=base.org_id,
        knowledge_base_id=base.id,
        source="upload",
        filename=accepted.filename,
        title=accepted.filename,
        mime=accepted.mime,
        size_bytes=len(data),
        digest=digest,
        content=data,
        status=PENDING,
        created_by=principal.email,
    )
    session.add(document)
    await session.flush()
    await record(
        session,
        principal,
        "knowledge.document.upload",
        org_id=base.org_id,
        target_type="knowledge_document",
        target_id=document.id,
        knowledge_base=base.slug,
        digest=digest,
    )
    dto = _document_dto(document)
    # The conversion claims the row: it must be committed before the task can see it.
    await session.commit()
    conversions.schedule(org, document.id)
    return dto


def _too_large(mib: int) -> ApiError:
    return ApiError(
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Payload too large", f"a document is at most {mib} MiB"
    )


async def _document(
    session: AsyncSession, base: KnowledgeBase, document_id: str, *, with_markdown: bool = False
) -> KnowledgeDocument:
    query = select(KnowledgeDocument).where(
        KnowledgeDocument.knowledge_base_id == base.id, KnowledgeDocument.id == document_id
    )
    if with_markdown:
        query = query.options(undefer(KnowledgeDocument.markdown))
    document = (await session.execute(query)).scalar_one_or_none()
    if document is None:
        raise not_found("Knowledge document", document_id)
    return document


@router.get(
    "/orgs/{org}/knowledge/{slug}/documents/{document_id}",
    response_model=KnowledgeDocumentDto,
    operation_id="getKnowledgeDocument",
)
async def get_knowledge_document(
    org: str, slug: Slug, document_id: str, session: Db, principal: Me
) -> KnowledgeDocumentDto:
    """The document and, once converted, its markdown."""
    _member(principal, org)
    base = await _base(session, principal, org, slug)
    return _document_dto(await _document(session, base, document_id, with_markdown=True), with_markdown=True)


@router.delete(
    "/orgs/{org}/knowledge/{slug}/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteKnowledgeDocument",
)
async def delete_knowledge_document(
    org: str, slug: Slug, document_id: str, session: Db, principal: Me
) -> None:
    base = await _base(session, principal, org, slug)
    _require(principal, org, base, KnowledgeAction.EDIT)
    document = await _document(session, base, document_id)
    await session.delete(document)
    await record(
        session,
        principal,
        "knowledge.document.delete",
        org_id=base.org_id,
        target_type="knowledge_document",
        target_id=document_id,
        knowledge_base=base.slug,
    )


@router.post(
    "/orgs/{org}/knowledge/{slug}/documents/{document_id}/convert",
    response_model=KnowledgeDocumentDto,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="convertKnowledgeDocument",
)
async def convert_knowledge_document(
    org: str, slug: Slug, document_id: str, session: Db, principal: Me
) -> KnowledgeDocumentDto:
    """Converts a document again — after a failure, or a converter upgrade."""
    base = await _base(session, principal, org, slug)
    _require(principal, org, base, KnowledgeAction.EDIT)
    document = await _document(session, base, document_id)
    if document.status in {PENDING, "converting"}:
        raise conflict(f"document `{document_id}` is already being converted")
    document.status, document.error = PENDING, None
    await session.flush()
    dto = _document_dto(document)
    await session.commit()
    conversions.schedule(org, document.id)
    return dto


@router.post(
    "/orgs/{org}/knowledge/{slug}/search",
    response_model=KnowledgeSearchResult,
    operation_id="searchKnowledgeBase",
)
async def search_knowledge_base(
    org: str, slug: Slug, body: KnowledgeSearchRequest, session: Db, principal: Me
) -> KnowledgeSearchResult:
    """The hybrid search of an `index` base, with its citations — what `knowledge_search` gives agents."""
    _member(principal, org)
    base = await _base(session, principal, org, slug)
    if base.serving_mode != "index":
        raise conflict(f"knowledge base `{slug}` is served as files: it has no index to search")
    result = await search(session, [base], body.query, k=body.k)
    return KnowledgeSearchResult.model_validate(
        {"mode": result.mode, "hits": [asdict(h) for h in result.hits]}
    )


@router.post(
    "/orgs/{org}/knowledge/{slug}/reindex",
    response_model=KnowledgeBaseDto,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="reindexKnowledgeBase",
)
async def reindex_knowledge_base(org: str, slug: Slug, session: Db, principal: Me) -> KnowledgeBaseDto:
    """Cuts and embeds every ready document again, in the background (after pgvector was installed,
    or the embedding model changed)."""
    base = await _base(session, principal, org, slug)
    _require(principal, org, base, KnowledgeAction.EDIT)
    if base.serving_mode != "index":
        raise conflict(f"knowledge base `{slug}` is served as files: switch it to `index` first")
    dto = await _dto(session, principal, org, base, with_documents=True)
    await session.commit()
    conversions.spawn("reindex", partial(reindex_base, org, base.id))
    return dto


@router.put(
    "/orgs/{org}/knowledge/{slug}/eval",
    response_model=KnowledgeBaseDto,
    operation_id="putKnowledgeEvalSet",
)
async def put_knowledge_eval_set(
    org: str, slug: Slug, body: KnowledgeEvalSet, session: Db, principal: Me
) -> KnowledgeBaseDto:
    """The base's eval set: questions, and the documents expected to answer each."""
    base = await _base(session, principal, org, slug)
    _require(principal, org, base, KnowledgeAction.EDIT)
    base.eval_set = [case.model_dump() for case in body.cases]
    base.eval_result = None
    await session.flush()
    return await _dto(session, principal, org, base)


@router.post(
    "/orgs/{org}/knowledge/{slug}/eval/run",
    response_model=KnowledgeBaseDto,
    operation_id="runKnowledgeEval",
)
async def run_knowledge_eval(org: str, slug: Slug, session: Db, principal: Me) -> KnowledgeBaseDto:
    """Runs the eval set against the index: recall@5, per question. Shown on the base's page."""
    base = await _base(session, principal, org, slug)
    _require(principal, org, base, KnowledgeAction.EDIT)
    if base.serving_mode != "index":
        raise conflict(f"knowledge base `{slug}` is served as files: it has no index to evaluate")
    if not base.eval_set:
        raise unprocessable("the eval set is empty: give questions and their expected documents first")
    base.eval_result = await evaluate(session, base, k=5)
    await session.flush()
    await record(
        session,
        principal,
        "knowledge.eval",
        org_id=base.org_id,
        target_type="knowledge_base",
        target_id=base.id,
        recall=base.eval_result["recall"],
    )
    return await _dto(session, principal, org, base)


# ───────────────────────────── sources (S33-04) ─────────────────────────────

_HTTPS = ("https://",)


async def _documents_connector(
    session: AsyncSession, organisation: Organization, name: str, source_kind: str
) -> OrgConnector:
    """The `documents` connector a source names, or the reason it is not one (422).

    Connector names are lowercase, so the lookup ignores case and says what the name should be;
    a connector of another kind or type is named for what it is rather than "not found".
    """
    rows = (
        (await session.execute(select(OrgConnector).where(OrgConnector.org_id == organisation.id)))
        .scalars()
        .all()
    )
    exact = [c for c in rows if c.name == name]
    folded = [c for c in rows if c.name.lower() == name.strip().lower()]
    match = (exact + folded)[0] if exact or folded else None
    if match is not None and match.kind != "documents":
        raise unprocessable(
            f"no `documents` connector `{name}` in {organisation.slug}: "
            f"`{match.name}` is a {match.kind}/{match.type} connector, not a documents one"
        )
    if match is not None and match.name != name:
        raise unprocessable(
            f"no `documents` connector `{name}` in {organisation.slug} "
            f'(names are lowercase: did you mean "{match.name}"?)'
        )
    if match is not None:
        allowed = SOURCE_CONNECTOR_TYPES.get(source_kind, frozenset())
        if match.type not in allowed:
            raise unprocessable(
                f"`{name}` is a documents/{match.type} connector: "
                f"a {source_kind} source needs {' or '.join(sorted(allowed))}"
            )
        return match
    names = sorted(c.name for c in rows if c.kind == "documents")
    listing = (
        f"documents connectors here: {', '.join(names)}"
        if names
        else "none: an org admin declares one in /admin/connectors"
    )
    raise unprocessable(f"no `documents` connector `{name}` in {organisation.slug} ({listing})")


def _sharepoint_config(config: dict[str, object]) -> dict[str, object]:
    if not (config.get("site_id") or config.get("drive_id")):
        raise unprocessable("a SharePoint or OneDrive source names a `site_id` or a `drive_id`")
    return {k: str(v) for k, v in config.items() if k in {"site_id", "drive_id", "folder"} and v}


_SPACE_KEY = re.compile(r"^[A-Z0-9~][A-Za-z0-9_~-]{0,254}$")


def _confluence_config(config: dict[str, object]) -> dict[str, object]:
    space = str(config.get("space_key") or "")
    if not _SPACE_KEY.match(space):
        raise unprocessable("a Confluence source names a `space_key` (letters and digits, e.g. ENG)")
    page = config.get("page_id")
    if page not in (None, "") and not re.fullmatch(r"\d{1,20}", str(page)):
        raise unprocessable("`page_id` is the number in the page's URL (/pages/<id>/…)")
    ids = config.get("page_ids")
    if ids not in (None, []):
        if page not in (None, ""):
            raise unprocessable("name `page_id` or `page_ids`, not both")
        if (
            not isinstance(ids, list)
            or not len(ids) > 0
            or len(ids) > 50
            or not all(isinstance(x, str | int) and re.fullmatch(r"\d{1,20}", str(x)) for x in ids)
        ):
            raise unprocessable("`page_ids` is a list of at most 50 page numbers (/pages/<id>/…)")
    for flag in ("include_root", "include_attachments", "descendants"):
        if flag in config and not isinstance(config[flag], bool):
            raise unprocessable(f"`{flag}` is true or false")
    if config.get("descendants") is False and config.get("include_root") is False:
        raise unprocessable("`descendants` false with `include_root` false leaves no page")
    labels = config.get("labels") or []
    if (
        not isinstance(labels, list)
        or len(labels) > 20
        or not all(isinstance(x, str) and 0 < len(x) <= 255 for x in labels)
    ):
        raise unprocessable("`labels` is a list of at most 20 labels")
    out: dict[str, object] = {"space_key": space}
    if page not in (None, ""):
        out["page_id"] = str(page)
    if isinstance(ids, list) and ids:
        out["page_ids"] = list(dict.fromkeys(str(x) for x in ids))
    if config.get("descendants") is False:
        out["descendants"] = False  # absent means true: a stored config reads as before
    out["include_root"] = bool(config.get("include_root", True))
    out["include_attachments"] = bool(config.get("include_attachments", False))
    if labels:
        out["labels"] = labels
    return out


def _web_config(config: dict[str, object]) -> dict[str, object]:
    url = _https_url(config, "web")
    exclude = config.get("exclude") or []
    if (
        not isinstance(exclude, list)
        or len(exclude) > 50
        or not all(isinstance(p, str) and p.startswith("/") and len(p) <= 200 for p in exclude)
    ):
        raise unprocessable("`exclude` is a list of at most 50 path globs, each starting with `/`")
    checked: dict[str, object] = {
        "url": url,
        "max_depth": max(0, min(int(str(config.get("max_depth", 2))), 5)),
        "max_pages": max(1, min(int(str(config.get("max_pages", 50))), 500)),
    }
    if exclude:
        checked["exclude"] = exclude
    if "include_documents" in config:
        if not isinstance(config["include_documents"], bool):
            raise unprocessable("`include_documents` is true or false")
        if config["include_documents"]:
            checked["include_documents"] = True
    return checked


def _git_config(config: dict[str, object]) -> dict[str, object]:
    url = _https_url(config, "git")
    ref = str(config.get("ref") or "main")
    if ref.startswith("-"):
        raise unprocessable(f"not a git ref: `{ref}`")
    paths = config.get("paths") or ["*.md"]
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        raise unprocessable("`paths` is a list of glob patterns")
    return {"url": url, "ref": ref, "paths": paths}


def _https_url(config: dict[str, object], kind: str) -> str:
    url = str(config.get("url") or "")
    if not url.startswith(_HTTPS):
        raise unprocessable(f"a {kind} source is reached over https: `{url}`")
    return url


#: One validator per source kind: the configuration it keeps, or the reason it is refused.
_SOURCE_VALIDATORS: dict[str, Callable[[dict[str, object]], dict[str, object]]] = {
    "sharepoint": _sharepoint_config,
    "confluence": _confluence_config,
    "web": _web_config,
    "git": _git_config,
}

_CONNECTED_WHAT = {"sharepoint": "SharePoint or OneDrive", "confluence": "Confluence", "git": "private git"}


async def _checked_source_config(
    session: AsyncSession,
    organisation: Organization,
    kind: str,
    connector: str | None,
    config: dict[str, object],
) -> dict[str, object]:
    """What a source may be: refused here, with its reason, rather than at its first sync."""
    if kind in CONNECTED_KINDS or (kind in OPTIONALLY_CONNECTED and connector):
        if not connector:
            raise unprocessable(f"a {_CONNECTED_WHAT.get(kind, kind)} source names its `documents` connector")
        await _documents_connector(session, organisation, connector, kind)
    checked = _SOURCE_VALIDATORS[kind](config)
    if kind in {"web", "git"}:
        # The organisation's network bounds (ADR 0070): a site its ceiling denies is not crawled, and
        # the refusal comes now (422, nothing an opening could fix), before the egress gate's 409.
        await check_platform_host(session, organisation.id, str(checked["url"]), f"{kind} source")
    return checked


async def _reachable_start(
    session: AsyncSession, organisation: Organization, kind: str, config: dict[str, object]
) -> None:
    """A web source's start URL answers, checked AFTER the egress gate (which may just have allowed
    its host) and INSIDE the organisation's policy: the guarded client fails closed without one
    (S39-28, seen on the dev tenant in `enforce`)."""
    if kind != "web" or fakes_enabled():
        return
    exclude = config.get("exclude") or []
    assert isinstance(exclude, list)
    try:
        async with (
            egress_scope(session, organisation, "api", "knowledge-source:web"),
            proxy_scope(session, organisation.id, ("knowledge_web",)),
        ):
            await _check_web_start(str(config["url"]), exclude)
    except OutboundProxyError as unusable:
        raise unprocessable(str(unusable)) from unusable


async def _check_web_start(url: str, exclude: list[str]) -> None:
    """A start URL that redirects outside the allow-list yields no page: say so now, with the target."""
    from choregos_adapters.documents import web
    from choregos_adapters.documents.base import SourceRefusedError

    async def resolver(host: str) -> list[str]:
        return await web.resolve(host)

    try:
        crawl = web.WebCrawlSource(url=url, exclude=exclude, resolver=resolver)
        try:
            await crawl.check_start()
        finally:
            await crawl.aclose()
    except SourceRefusedError as exc:
        raise unprocessable(str(exc)) from exc
    except httpx.HTTPError as exc:
        raise unprocessable(f"`{url}` is not reachable: {exc}") from exc


async def _egress_gate(
    session: AsyncSession,
    principal: Me,
    organisation: Organization,
    kind: str,
    connector: str | None,
    config: dict[str, object],
    name: str,
    allow_egress: bool,
) -> None:
    """A source's hosts must be in the organisation's egress allowlist (S39-22): the modal's 409,
    or, for an administrator who says so, opened in this same transaction. A connected source
    (`sharepoint`, `confluence`) calls the hosts its connector declares."""
    targets = knowledge_source_targets(kind, dict(config))
    if connector and kind in CONNECTED_KINDS:
        found = (
            await session.execute(
                select(OrgConnector).where(
                    OrgConnector.org_id == organisation.id, OrgConnector.name == connector
                )
            )
        ).scalar_one_or_none()
        if found is not None:
            targets += targets_of_connector(found.kind, found.type, dict(found.config or {}))
    await ensure_or_409(
        session,
        principal,
        organisation,
        targets,
        allow_egress=allow_egress,
        origin=f"knowledge-source:{name}",
        reason=f"the {kind} knowledge source `{name}`",
    )


async def _source(session: AsyncSession, base: KnowledgeBase, source_id: str) -> KnowledgeSource:
    source = (
        await session.execute(
            select(KnowledgeSource).where(
                KnowledgeSource.knowledge_base_id == base.id, KnowledgeSource.id == source_id
            )
        )
    ).scalar_one_or_none()
    if source is None:
        raise not_found("Knowledge source", source_id)
    return source


def _checked_refresh(mode: str, cron: str | None, timezone: str) -> tuple[str | None, str]:
    """The refresh of a source: refused here, with its reason (a bad cron, a gap under 5 minutes, an
    unknown time zone). `interval` and `manual` keep no cron."""
    from ..knowledge.refresh import check_cron

    if mode != "cron":
        return None, timezone if _zone_known(timezone) else _refuse_zone(timezone)
    if not cron:
        raise unprocessable("a `cron` refresh needs a `refresh_cron`")
    try:
        check_cron(cron, timezone)
    except ValueError as error:
        raise unprocessable(str(error)) from error
    return " ".join(cron.split()), timezone


def _zone_known(name: str) -> bool:
    from zoneinfo import ZoneInfo

    try:
        ZoneInfo(name)
    except Exception:
        return False
    return True


def _refuse_zone(name: str) -> str:
    raise unprocessable(f"unknown time zone `{name}`")


async def _start_sync_loop(org: str, source: KnowledgeSource) -> None:
    await get_temporal().start_knowledge_sync(
        knowledge_sync_id(source.id),
        {"org": org, "source_id": source.id, "interval_seconds": source.interval_minutes * 60},
    )


@router.post(
    "/orgs/{org}/knowledge/{slug}/sources",
    response_model=KnowledgeSourceDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="createKnowledgeSource",
)
async def create_knowledge_source(
    org: str, slug: Slug, body: KnowledgeSourceCreate, session: Db, principal: Me
) -> KnowledgeSourceDto:
    """SharePoint/OneDrive (through a `documents` connector), an allow-listed web site, or a git
    repository; synced at once, then every `interval_minutes`. `agent:manage`."""
    base = await _base(session, principal, org, slug)
    _require(principal, org, base, KnowledgeAction.EDIT)
    organisation = await _organisation(session, org)
    config = await _checked_source_config(session, organisation, body.kind, body.connector, body.config)
    await _egress_gate(
        session, principal, organisation, body.kind, body.connector, config, body.name, body.allow_egress
    )
    await _reachable_start(session, organisation, body.kind, config)
    refresh_cron, refresh_timezone = _checked_refresh(
        body.refresh_mode, body.refresh_cron, body.refresh_timezone
    )
    source = KnowledgeSource(
        org_id=base.org_id,
        knowledge_base_id=base.id,
        kind=body.kind,
        name=body.name,
        connector=body.connector if body.kind in CONNECTED_KINDS | OPTIONALLY_CONNECTED else None,
        config=config,
        interval_minutes=body.interval_minutes,
        refresh_mode=body.refresh_mode,
        refresh_cron=refresh_cron,
        refresh_timezone=refresh_timezone,
        next_sync_at=None,  # due: the first pass syncs
        status="idle",
        created_by=principal.email,
    )
    session.add(source)
    await session.flush()
    await record(
        session,
        principal,
        "knowledge.source.create",
        org_id=base.org_id,
        target_type="knowledge_source",
        target_id=source.id,
        kind=source.kind,
    )
    dto = await _source_dto(session, source)
    await session.commit()
    await _start_sync_loop(org, source)
    return dto


@router.patch(
    "/orgs/{org}/knowledge/{slug}/sources/{source_id}",
    response_model=KnowledgeSourceDto,
    operation_id="updateKnowledgeSource",
)
async def update_knowledge_source(
    org: str, slug: Slug, source_id: str, body: KnowledgeSourcePatch, session: Db, principal: Me
) -> KnowledgeSourceDto:
    base = await _base(session, principal, org, slug)
    _require(principal, org, base, KnowledgeAction.EDIT)
    source = await _source(session, base, source_id)
    if body.name is not None:
        source.name = body.name
    refresh_changed = any(
        value is not None
        for value in (body.interval_minutes, body.refresh_mode, body.refresh_cron, body.refresh_timezone)
    )
    if refresh_changed:
        if body.interval_minutes is not None:
            source.interval_minutes = body.interval_minutes
        mode = body.refresh_mode or source.refresh_mode
        cron = body.refresh_cron if body.refresh_cron is not None else source.refresh_cron
        zone = body.refresh_timezone or source.refresh_timezone
        source.refresh_cron, source.refresh_timezone = _checked_refresh(mode, cron, zone)
        source.refresh_mode = mode
        # A schedule never synced has no "last": it stays due. Otherwise the next sync moves now.
        now = refresh.utcnow()
        source.next_sync_at = (
            None
            if source.last_synced_at is None
            else refresh.next_sync_after(source, now if mode == "cron" else source.last_synced_at)
        )
    if body.config is not None:
        organisation = await _organisation(session, org)
        source.config = await _checked_source_config(
            session, organisation, source.kind, source.connector, body.config
        )
        await _egress_gate(
            session,
            principal,
            organisation,
            source.kind,
            source.connector,
            source.config,
            source.name,
            body.allow_egress,
        )
        await _reachable_start(session, organisation, source.kind, source.config)
        # Another site, URL or ref: the next sync starts from scratch.
        source.cursor = None
    if refresh_changed:
        await record(
            session,
            principal,
            "knowledge.source.refresh",
            org_id=base.org_id,
            target_type="knowledge_source",
            target_id=source.id,
            mode=source.refresh_mode,
        )
    await session.flush()
    dto = await _source_dto(session, source)
    if refresh_changed:
        # Takes effect now, not at the end of the current sleep: the woken pass is "not due" (a
        # reschedule) unless a sync was requested.
        await session.commit()
        await _start_sync_loop(org, source)
        with contextlib.suppress(Exception):
            await get_temporal().signal(knowledge_sync_id(source.id), "sync_now", {})
    return dto


@router.delete(
    "/orgs/{org}/knowledge/{slug}/sources/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteKnowledgeSource",
)
async def delete_knowledge_source(org: str, slug: Slug, source_id: str, session: Db, principal: Me) -> None:
    """The source and the documents it brought (and their chunks) leave the base."""
    base = await _base(session, principal, org, slug)
    _require(principal, org, base, KnowledgeAction.EDIT)
    source = await _source(session, base, source_id)
    await session.execute(delete(KnowledgeDocument).where(KnowledgeDocument.source_id == source.id))
    await session.delete(source)
    await record(
        session,
        principal,
        "knowledge.source.delete",
        org_id=base.org_id,
        target_type="knowledge_source",
        target_id=source_id,
    )
    await session.commit()
    # The loop finds its source gone at its next pass and stops; told now, it stops at once.
    with contextlib.suppress(Exception):  # a loop that does not run any more has nothing to stop
        await get_temporal().signal(knowledge_sync_id(source_id), "stop", {})


@router.post(
    "/orgs/{org}/knowledge/{slug}/sources/{source_id}/sync",
    response_model=KnowledgeSourceDto,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="syncKnowledgeSource",
)
async def sync_knowledge_source(
    org: str, slug: Slug, source_id: str, session: Db, principal: Me
) -> KnowledgeSourceDto:
    """Syncs now rather than at the end of the interval. `agent:manage`."""
    base = await _base(session, principal, org, slug)
    _require(principal, org, base, KnowledgeAction.EDIT)
    source = await _source(session, base, source_id)
    source.sync_requested_at = refresh.utcnow()
    dto = await _source_dto(session, source)
    await session.commit()
    await _start_sync_loop(org, source)
    await get_temporal().signal(knowledge_sync_id(source.id), "sync_now", {})
    return dto


@router.post(
    "/orgs/{org}/knowledge/{slug}/sync",
    response_model=KnowledgeBaseDto,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="syncKnowledgeBase",
)
async def sync_knowledge_base(org: str, slug: Slug, session: Db, principal: Me) -> KnowledgeBaseDto:
    """Syncs every source of the base now: one gesture for the whole base. Whoever edits the base
    (ADR 0051 §12, ADR 0081): it was `agent:manage` only, which the per-source routes never were."""
    base = await _base(session, principal, org, slug)
    _require(principal, org, base, KnowledgeAction.EDIT)
    sources = (
        (await session.execute(select(KnowledgeSource).where(KnowledgeSource.knowledge_base_id == base.id)))
        .scalars()
        .all()
    )
    now = refresh.utcnow()
    for source in sources:
        source.sync_requested_at = now
    await record(
        session,
        principal,
        "knowledge.sync",
        org_id=base.org_id,
        target_type="knowledge_base",
        target_id=base.id,
        sources=len(sources),
    )
    dto = await _dto(session, principal, org, base)
    await session.commit()
    for source in sources:
        await _start_sync_loop(org, source)
        await get_temporal().signal(knowledge_sync_id(source.id), "sync_now", {})
    return dto


@router.get(
    "/orgs/{org}/knowledge/{slug}/distill/estimate",
    response_model=KnowledgeDistillEstimate,
    operation_id="estimateKnowledgeDistillation",
)
async def estimate_knowledge_distillation(
    org: str,
    slug: Slug,
    session: Db,
    principal: Me,
    exclude: Annotated[list[str] | None, Query(max_length=20)] = None,
) -> KnowledgeDistillEstimate:
    """What distilling the base would read and cost, with no model call. Same rights as `distill`."""
    base = await _base(session, principal, org, slug)
    _require(principal, org, base, KnowledgeAction.DISTILL)
    from ..knowledge.distill import estimate_for

    estimate = await estimate_for(session, base, exclude or [])
    return KnowledgeDistillEstimate(**estimate.values)


async def _start_distillation(
    session: Any,
    principal: Any,
    org: str,
    slug: str,
    base: Any,
    exclude: list[str],
    max_usd: float | None,
    *,
    audit: str,
) -> KnowledgeBaseDto:
    """Starts a run on `base`: the estimate against the cap, the new record (the previous one joins
    its `history`), the audit line, and the workflow after the commit."""
    from ..knowledge.distill import estimate_for, estimated_total, is_running, new_run

    if is_running(base.distillation):
        raise conflict(f"knowledge base `{slug}` is already being distilled")
    if max_usd is not None:
        total = estimated_total(await estimate_for(session, base, exclude))
        if total is not None and total > max_usd:
            raise ApiError(
                status.HTTP_409_CONFLICT,
                "Conflict",
                f"the estimated cost (${total:.2f}) is above the ${max_usd:.2f} asked",
                extra={"code": "distill_over_budget"},
            )
    started_at = refresh.utcnow().isoformat()
    base.distillation = new_run(base.distillation, principal.email, started_at, exclude, max_usd)
    await session.flush()
    await record(
        session,
        principal,
        audit,
        org_id=base.org_id,
        target_type="knowledge_base",
        target_id=base.id,
    )
    dto = await _dto(session, principal, org, base)
    await session.commit()
    await get_temporal().start_knowledge_distill(
        knowledge_distill_id(base.id, started_at),
        {
            "org": org,
            "base_id": base.id,
            "requested_by": principal.email,
            "exclude": exclude,
            "max_usd": max_usd,
        },
    )
    return dto


@router.post(
    "/orgs/{org}/knowledge/{slug}/distill",
    response_model=KnowledgeBaseDto,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="distillKnowledgeBase",
)
async def distill_knowledge_base(
    org: str, slug: Slug, session: Db, principal: Me, body: KnowledgeDistillRequest | None = None
) -> KnowledgeBaseDto:
    """The `knowledge-distiller` compiles the base into a principles skill, in the background; the
    version waits for a person's approval (`pending_review`). `agent:manage`."""
    base = await _base(session, principal, org, slug)
    _require(principal, org, base, KnowledgeAction.DISTILL)
    return await _start_distillation(
        session,
        principal,
        org,
        slug,
        base,
        list(body.exclude) if body else [],
        body.max_usd if body else None,
        audit="knowledge.distill",
    )


@router.post(
    "/orgs/{org}/knowledge/{slug}/distill/relaunch",
    response_model=KnowledgeBaseDto,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="relaunchKnowledgeDistillation",
)
async def relaunch_knowledge_distillation(
    org: str, slug: Slug, session: Db, principal: Me, body: KnowledgeDistillRequest | None = None
) -> KnowledgeBaseDto:
    """Starts the last distillation again, whatever its outcome (S42-10d, ADR 0092). Without a body it
    keeps the last run's `exclude` and `max_usd`. The parts already stored are not called again."""
    base = await _base(session, principal, org, slug)
    _require(principal, org, base, KnowledgeAction.DISTILL)
    last = base.distillation
    if not last:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "Conflict",
            f"knowledge base `{slug}` has no distillation to relaunch",
            extra={"code": "distill_nothing_to_relaunch"},
        )
    exclude = list(body.exclude) if body else list(last.get("exclude") or [])
    max_usd = body.max_usd if body else last.get("max_usd")
    return await _start_distillation(
        session, principal, org, slug, base, exclude, max_usd, audit="knowledge.distill.relaunch"
    )


@router.post(
    "/orgs/{org}/knowledge/{slug}/distill/stop",
    response_model=KnowledgeBaseDto,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="stopKnowledgeDistillation",
)
async def stop_knowledge_distillation(org: str, slug: Slug, session: Db, principal: Me) -> KnowledgeBaseDto:
    """Stops the distillation that is running (S42-10d, ADR 0092): the record says `stopped` at once,
    the workflow is cancelled after the commit. What was spent stays booked; nothing is published."""
    from ..knowledge.distill import is_running, stopped_record

    base = await _base(session, principal, org, slug)
    _require(principal, org, base, KnowledgeAction.DISTILL)
    before = base.distillation
    if not before or not is_running(before):
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "Conflict",
            f"knowledge base `{slug}` is not being distilled",
            extra={"code": "distill_not_running"},
        )
    base.distillation = stopped_record(before, principal.email)
    await session.flush()
    await record(
        session,
        principal,
        "knowledge.distill.stop",
        org_id=base.org_id,
        target_type="knowledge_base",
        target_id=base.id,
    )
    dto = await _dto(session, principal, org, base)
    await session.commit()
    try:
        await get_temporal().cancel(knowledge_distill_id(base.id, str(before.get("started_at") or "")))
    except Exception as error:  # the record says stopped; the run's next call refuses on it anyway
        log.warning("knowledge.distill.cancel_failed", base=base.id, error=str(error)[:300])
    return dto
