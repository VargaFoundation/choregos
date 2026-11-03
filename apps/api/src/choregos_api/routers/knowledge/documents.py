# SPDX-License-Identifier: Apache-2.0
"""Documents of a knowledge base: upload, read, delete and convert (ADR 0051)."""

from __future__ import annotations

import hashlib

from fastapi import Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer
from starlette.datastructures import UploadFile

from ...audit import record
from ...config import get_settings
from ...db.models import (
    KnowledgeBase,
    KnowledgeDocument,
)
from ...deps import Db, Me
from ...errors import ApiError, conflict, not_found, unprocessable
from ...knowledge.access import (
    KnowledgeAction,
)
from ...knowledge.conversion import PENDING, conversions
from ...knowledge.formats import DocumentRefusedError, accept
from ...schemas.knowledge import (
    KnowledgeDocumentDto,
)
from ._common import _base, _document_dto, _member, _require
from ._router import Slug, router

#: What a multipart envelope adds around the file, at most, before the size cap refuses it.
ENVELOPE = 64 * 1024


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
