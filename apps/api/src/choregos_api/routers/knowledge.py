# SPDX-License-Identifier: Apache-2.0
"""Knowledge bases of the organisation (ADR 0051).

A knowledge base is a curated corpus a human chose; its documents are uploaded here, answered 202
`pending`, and converted to markdown in the background (`knowledge.conversion`), never inside the
request. Curating is the organisation administrator's (`agent:manage`); reading follows the base's
scope (`knowledge.access`).
"""

from __future__ import annotations

import contextlib
import hashlib
from dataclasses import asdict
from functools import partial
from typing import Annotated

from fastapi import APIRouter, Path, Request, Response, status
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
    KnowledgeSource,
    Organization,
    OrgConnector,
    Project,
)
from ..deps import Db, Me
from ..errors import ApiError, conflict, forbidden, not_found, unprocessable
from ..knowledge.access import can_manage, visible_to
from ..knowledge.conversion import PENDING, conversions
from ..knowledge.formats import DocumentRefusedError, accept
from ..knowledge.index import evaluate, reindex_base, search, search_mode
from ..knowledge.sync import SOURCE_CONNECTOR_TYPES
from ..schemas.knowledge import (
    KnowledgeBaseCreate,
    KnowledgeBaseDto,
    KnowledgeBasePatch,
    KnowledgeDocumentDto,
    KnowledgeEvalSet,
    KnowledgeSearchRequest,
    KnowledgeSearchResult,
    KnowledgeSourceCreate,
    KnowledgeSourceDto,
    KnowledgeSourcePatch,
)
from ..temporal import get_temporal, knowledge_sync_id

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
        source_id=document.source_id,
        source_url=document.source_url,
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
        index=await _index_state(session, base) if base.serving_mode == "index" else None,
        eval_set=list(base.eval_set or []),
        eval_result=base.eval_result,
        sources=[await _source_dto(session, source) for source in await _sources(session, base)]
        if with_documents
        else None,
        distillation=base.distillation,
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
    reindex = changes.get("serving_mode") == "index" and base.serving_mode != "index"
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
    dto = await _dto(session, base, with_documents=True)
    if reindex:
        # Switched to `index`: its ready documents are cut and embedded in the background.
        await session.commit()
        conversions.spawn("reindex", partial(reindex_base, org, base.id))
    return dto


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
    _manage(principal, org)
    base = await _base(session, principal, org, slug)
    if base.serving_mode != "index":
        raise conflict(f"knowledge base `{slug}` is served as files: switch it to `index` first")
    dto = await _dto(session, base, with_documents=True)
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
    _manage(principal, org)
    base = await _base(session, principal, org, slug)
    base.eval_set = [case.model_dump() for case in body.cases]
    base.eval_result = None
    await session.flush()
    return await _dto(session, base)


@router.post(
    "/orgs/{org}/knowledge/{slug}/eval/run",
    response_model=KnowledgeBaseDto,
    operation_id="runKnowledgeEval",
)
async def run_knowledge_eval(org: str, slug: Slug, session: Db, principal: Me) -> KnowledgeBaseDto:
    """Runs the eval set against the index: recall@5, per question. Shown on the base's page."""
    _manage(principal, org)
    base = await _base(session, principal, org, slug)
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
    return await _dto(session, base)


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


async def _checked_source_config(
    session: AsyncSession,
    organisation: Organization,
    kind: str,
    connector: str | None,
    config: dict[str, object],
) -> dict[str, object]:
    """What a source may be: refused here, with its reason, rather than at its first sync."""
    if kind == "sharepoint":
        if not connector:
            raise unprocessable("a SharePoint or OneDrive source names its `documents` connector")
        await _documents_connector(session, organisation, connector, kind)
        if not (config.get("site_id") or config.get("drive_id")):
            raise unprocessable("a SharePoint or OneDrive source names a `site_id` or a `drive_id`")
        return {k: str(v) for k, v in config.items() if k in {"site_id", "drive_id", "folder"} and v}
    url = str(config.get("url") or "")
    if not url.startswith(_HTTPS):
        raise unprocessable(f"a {kind} source is reached over https: `{url}`")
    if kind == "web":
        return {
            "url": url,
            "max_depth": max(0, min(int(str(config.get("max_depth", 2))), 5)),
            "max_pages": max(1, min(int(str(config.get("max_pages", 50))), 500)),
        }
    ref = str(config.get("ref") or "main")
    if ref.startswith("-"):
        raise unprocessable(f"not a git ref: `{ref}`")
    paths = config.get("paths") or ["*.md"]
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        raise unprocessable("`paths` is a list of glob patterns")
    return {"url": url, "ref": ref, "paths": paths}


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
    _manage(principal, org)
    base = await _base(session, principal, org, slug)
    organisation = await _organisation(session, org)
    config = await _checked_source_config(session, organisation, body.kind, body.connector, body.config)
    source = KnowledgeSource(
        org_id=base.org_id,
        knowledge_base_id=base.id,
        kind=body.kind,
        name=body.name,
        connector=body.connector if body.kind == "sharepoint" else None,
        config=config,
        interval_minutes=body.interval_minutes,
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
    _manage(principal, org)
    base = await _base(session, principal, org, slug)
    source = await _source(session, base, source_id)
    if body.name is not None:
        source.name = body.name
    if body.interval_minutes is not None:
        source.interval_minutes = body.interval_minutes
    if body.config is not None:
        source.config = await _checked_source_config(
            session, await _organisation(session, org), source.kind, source.connector, body.config
        )
        # Another site, URL or ref: the next sync starts from scratch.
        source.cursor = None
    await session.flush()
    return await _source_dto(session, source)


@router.delete(
    "/orgs/{org}/knowledge/{slug}/sources/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteKnowledgeSource",
)
async def delete_knowledge_source(org: str, slug: Slug, source_id: str, session: Db, principal: Me) -> None:
    """The source and the documents it brought (and their chunks) leave the base."""
    _manage(principal, org)
    base = await _base(session, principal, org, slug)
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
    _manage(principal, org)
    base = await _base(session, principal, org, slug)
    source = await _source(session, base, source_id)
    dto = await _source_dto(session, source)
    await session.commit()
    await _start_sync_loop(org, source)
    await get_temporal().signal(knowledge_sync_id(source.id), "sync_now", {})
    return dto


@router.post(
    "/orgs/{org}/knowledge/{slug}/distill",
    response_model=KnowledgeBaseDto,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="distillKnowledgeBase",
)
async def distill_knowledge_base(org: str, slug: Slug, session: Db, principal: Me) -> KnowledgeBaseDto:
    """The `knowledge-distiller` compiles the base into a principles skill, in the background; the
    version waits for a person's approval (`pending_review`). `agent:manage`."""
    from ..knowledge.distill import distill

    _manage(principal, org)
    base = await _base(session, principal, org, slug)
    if (base.distillation or {}).get("status") == "running":
        raise conflict(f"knowledge base `{slug}` is already being distilled")
    base.distillation = {"status": "running", "requested_by": principal.email}
    await session.flush()
    await record(
        session,
        principal,
        "knowledge.distill",
        org_id=base.org_id,
        target_type="knowledge_base",
        target_id=base.id,
    )
    dto = await _dto(session, base)
    await session.commit()
    conversions.spawn("distill", partial(distill, org, base.id, principal.email))
    return dto
