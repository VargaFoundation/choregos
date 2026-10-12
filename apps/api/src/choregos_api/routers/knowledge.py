# SPDX-License-Identifier: Apache-2.0
"""Knowledge bases of the organisation (ADR 0051).

A knowledge base is a curated corpus a human chose; its documents are uploaded here, answered 202
`pending`, and converted to markdown in the background (`knowledge.conversion`), never inside the
request. Curating is the organisation administrator's (`agent:manage`); reading follows the base's
scope (`knowledge.access`).
"""

from __future__ import annotations

import hashlib
from typing import Annotated

from fastapi import APIRouter, Path, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer
from starlette.datastructures import UploadFile

from ..audit import record
from ..config import get_settings
from ..db.models import KnowledgeBase, KnowledgeDocument, Organization, Project
from ..deps import Db, Me
from ..errors import ApiError, conflict, forbidden, not_found, unprocessable
from ..knowledge.access import can_manage, visible_to
from ..knowledge.conversion import PENDING, conversions
from ..knowledge.formats import DocumentRefusedError, accept
from ..schemas.knowledge import (
    KnowledgeBaseCreate,
    KnowledgeBaseDto,
    KnowledgeBasePatch,
    KnowledgeDocumentDto,
)

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


async def _base(session: AsyncSession, principal: Me, org: str, slug: str) -> KnowledgeBase:
    """The base, if the caller may read it; one it may not read does not exist for it (404)."""
    organisation = await _organisation(session, org)
    base = (
        await session.execute(
            select(KnowledgeBase).where(KnowledgeBase.org_id == organisation.id, KnowledgeBase.slug == slug)
        )
    ).scalar_one_or_none()
    if base is None or not visible_to(principal, org, base):
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
        created_by=document.created_by,
        created_at=document.created_at,
        converted_at=document.converted_at,
        markdown=document.markdown if with_markdown else None,
    )


async def _dto(
    session: AsyncSession, base: KnowledgeBase, *, with_documents: bool = False
) -> KnowledgeBaseDto:
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
    )


@router.get("/orgs/{org}/knowledge", response_model=list[KnowledgeBaseDto], operation_id="listKnowledgeBases")
async def list_knowledge_bases(org: str, session: Db, principal: Me) -> list[KnowledgeBaseDto]:
    _member(principal, org)
    organisation = await _organisation(session, org)
    bases = (
        await session.execute(
            select(KnowledgeBase).where(KnowledgeBase.org_id == organisation.id).order_by(KnowledgeBase.slug)
        )
    ).scalars()
    return [await _dto(session, base) for base in bases if visible_to(principal, org, base)]


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
    return await _dto(session, base, with_documents=True)


@router.get("/orgs/{org}/knowledge/{slug}", response_model=KnowledgeBaseDto, operation_id="getKnowledgeBase")
async def get_knowledge_base(org: str, slug: Slug, session: Db, principal: Me) -> KnowledgeBaseDto:
    _member(principal, org)
    return await _dto(session, await _base(session, principal, org, slug), with_documents=True)


@router.patch(
    "/orgs/{org}/knowledge/{slug}", response_model=KnowledgeBaseDto, operation_id="updateKnowledgeBase"
)
async def update_knowledge_base(
    org: str, slug: Slug, body: KnowledgeBasePatch, session: Db, principal: Me
) -> KnowledgeBaseDto:
    _manage(principal, org)
    base = await _base(session, principal, org, slug)
    changes = body.model_dump(exclude_unset=True)
    scope = changes.get("scope", base.scope)
    projects = changes.get("projects", base.projects) or []
    groups = changes.get("groups", base.groups) or []
    if scope == "projects" and not projects:
        raise unprocessable("scope `projects` names at least one project")
    if scope == "groups" and not groups:
        raise unprocessable("scope `groups` names at least one group")
    await _known_projects(session, await _organisation(session, org), projects if scope == "projects" else [])
    for field in ("name", "description", "serving_mode", "status"):
        if field in changes and changes[field] is not None:
            setattr(base, field, changes[field])
    base.scope = scope
    base.projects = sorted(set(projects)) if scope == "projects" else []
    base.groups = sorted(set(groups)) if scope == "groups" else []
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
    return await _dto(session, base, with_documents=True)


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
    _manage(principal, org)
    settings = get_settings()
    cap = settings.knowledge_max_upload_mib * 1024 * 1024
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cap + ENVELOPE:
        raise _too_large(settings.knowledge_max_upload_mib)
    base = await _base(session, principal, org, slug)
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
    _manage(principal, org)
    base = await _base(session, principal, org, slug)
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
    _manage(principal, org)
    base = await _base(session, principal, org, slug)
    document = await _document(session, base, document_id)
    if document.status in {PENDING, "converting"}:
        raise conflict(f"document `{document_id}` is already being converted")
    document.status, document.error = PENDING, None
    await session.flush()
    dto = _document_dto(document)
    await session.commit()
    conversions.schedule(org, document.id)
    return dto
