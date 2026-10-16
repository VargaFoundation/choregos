# SPDX-License-Identifier: Apache-2.0
"""Syncing a knowledge source into its knowledge base (S33-04, ADR 0051).

One sync: ask the source what changed since its cursor, fetch what is new or changed, convert and
(for an `index` base) index it, delete what the source no longer holds, then keep the new cursor.
Idempotent (ADR 0008): the cursor moves only once the changes are applied, so a sync that fails
half-way is replayed from the same point, and replaying it changes nothing twice — an item whose
version is already stored is not fetched again, a deletion of what is gone is a no-op.

A source that lists everything (`full`: a crawl, a commit) deletes what it does not list — unless
some of its pages could not be read this time: then nothing is deleted, a 500 is not a deletion.
"""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
from typing import Any

from choregos_adapters.documents.base import Changes, DocumentSource, ItemUnreadableError, SourceItem
from choregos_adapters.registry import build, fakes_enabled
from choregos_core import utcnow
from choregos_core.egress import pop_scope, push_scope
from choregos_core.redaction import redact
from sqlalchemy import delete, func, select

from ..config import get_settings
from ..db.models import KnowledgeBase, KnowledgeDocument, KnowledgeSource, Organization, OrgConnector
from ..db.session import session_scope
from ..logging import get_logger
from ..secret_store import resolved_configuration
from ..services.egress import load_policy
from .conversion import PENDING, convert_document
from .formats import FORMATS, DocumentRefusedError, accept

logger = get_logger("choregos.knowledge.sync")

KINDS = ("sharepoint", "confluence", "web", "git")
#: kinds opened through an organisation `documents` connector, which holds the credentials
CONNECTED_KINDS = frozenset({"sharepoint", "confluence"})
#: kinds that read public material without a connector, and a private one with it
OPTIONALLY_CONNECTED = frozenset({"git"})
#: the connector types a connected source kind may be served by
SOURCE_CONNECTOR_TYPES: dict[str, frozenset[str]] = {
    "sharepoint": frozenset({"sharepoint"}),
    "confluence": frozenset({"confluence"}),
    "git": frozenset({"git"}),
}


class SourceUnavailableError(RuntimeError):
    """The source cannot be opened (its connector is gone, its configuration refused)."""


async def open_source(session: Any, source: KnowledgeSource) -> DocumentSource:
    """The adapter of a source: under `CHOREGOS_FAKES`, its scripted fake."""
    config = source.config or {}
    if source.kind in CONNECTED_KINDS or (source.kind in OPTIONALLY_CONNECTED and source.connector):
        connector = (
            await session.execute(
                select(OrgConnector).where(
                    OrgConnector.org_id == source.org_id,
                    OrgConnector.name == source.connector,
                    OrgConnector.kind == "documents",
                )
            )
        ).scalar_one_or_none()
        if connector is None:
            raise SourceUnavailableError(f"the `documents` connector `{source.connector}` does not exist")
        allowed = SOURCE_CONNECTOR_TYPES.get(source.kind, frozenset())
        if connector.type not in allowed:
            raise SourceUnavailableError(
                f"the connector `{source.connector}` is a documents/{connector.type} connector: "
                f"a {source.kind} source needs {' or '.join(sorted(allowed))}"
            )
        resolved = await resolved_configuration(
            session, source.org_id, connector.kind, connector.type, connector.config, connector.secret_refs
        )
        return build("documents", connector.type, resolved).source(  # type: ignore[no-any-return]
            dict(config), key=source.id
        )
    if fakes_enabled():
        from choregos_adapters.fakes.documents import fake_source

        return fake_source(source.id)
    if source.kind == "web":
        from choregos_adapters.documents.web import WebCrawlSource

        return WebCrawlSource(
            url=str(config["url"]),
            max_depth=int(config.get("max_depth", 2)),
            max_pages=int(config.get("max_pages", 50)),
            exclude=config.get("exclude"),
        )
    if source.kind == "git":
        from choregos_adapters.documents.git import GitSource

        return GitSource(
            url=str(config["url"]), ref=str(config.get("ref", "main")), paths=config.get("paths")
        )
    raise SourceUnavailableError(f"unknown source kind `{source.kind}`")


def _summary() -> dict[str, Any]:
    return {"added": 0, "updated": 0, "deleted": 0, "unchanged": 0, "skipped": 0, "skipped_reasons": []}


def _skip(summary: dict[str, Any], item: SourceItem, reason: str) -> None:
    summary["skipped"] += 1
    if len(summary["skipped_reasons"]) < 20:
        summary["skipped_reasons"].append(f"{item.name}: {reason}")


async def _apply_item(
    session: Any, source: KnowledgeSource, base: KnowledgeBase, adapter: DocumentSource, item: SourceItem,
    existing: KnowledgeDocument | None, summary: dict[str, Any], cap: int,
) -> str | None:  # fmt: skip
    """Stores one item; returns the id of a document to convert, if any."""
    if PurePosixPath(item.name.lower()).suffix not in FORMATS:
        _skip(summary, item, "format not converted")
        return None
    if existing is not None and existing.source_version == item.version:
        summary["unchanged"] += 1
        return None
    if item.size is not None and item.size > cap:
        _skip(summary, item, f"larger than {cap // (1024 * 1024)} MiB")
        return None
    try:
        data = await adapter.fetch(item)
    except ItemUnreadableError as unreadable:
        _skip(summary, item, f"unreadable: {unreadable}")
        return None
    if len(data) > cap:
        _skip(summary, item, f"larger than {cap // (1024 * 1024)} MiB")
        return None
    try:
        accepted = accept(item.name, data)
    except DocumentRefusedError as refusal:
        _skip(summary, item, str(refusal))
        return None
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    if existing is not None and existing.digest == digest:
        existing.source_version, existing.source_url = item.version, item.url
        summary["unchanged"] += 1
        return None
    twin = (
        await session.execute(
            select(KnowledgeDocument.filename).where(
                KnowledgeDocument.knowledge_base_id == base.id, KnowledgeDocument.digest == digest
            )
        )
    ).scalar_one_or_none()
    if twin is not None:
        _skip(summary, item, f"same content as `{twin}`")
        return None
    document = existing or KnowledgeDocument(
        org_id=base.org_id, knowledge_base_id=base.id, source_id=source.id, source_ref=item.ref,
        created_by=f"source:{source.name}",
    )  # fmt: skip
    document.source = source.kind
    document.filename, document.title, document.mime = accepted.filename, accepted.filename, accepted.mime
    document.size_bytes, document.digest, document.content = len(data), digest, data
    document.source_version, document.source_url = item.version, item.url
    document.status, document.error, document.markdown = PENDING, None, None
    session.add(document)
    await session.flush()
    summary["updated" if existing is not None else "added"] += 1
    return str(document.id)


async def _deleted(changes: Changes, known: dict[str, KnowledgeDocument]) -> set[str]:
    gone = set(changes.deleted)
    if changes.full and not changes.unreadable:
        listed = {item.ref for item in changes.items}
        gone |= {ref for ref in known if ref not in listed}
    return gone & set(known)


async def _enter_egress_scope(session: Any, org: str, family: str, source: KnowledgeSource) -> Any:
    """Every call this sync makes is checked against the organisation's egress allowlist (S39-22)."""
    organisation = (
        await session.execute(select(Organization).where(Organization.slug == org))
    ).scalar_one_or_none()
    if organisation is None:
        return None
    policy = await load_policy(session, organisation)
    return push_scope(policy, family, f"knowledge-source:{source.name}")


async def sync_source(
    org: str, source_id: str, *, adapter: DocumentSource | None = None, family: str = "workers"
) -> dict[str, Any]:
    """One sync of one source; returns its summary. A source removed meanwhile answers `gone`.

    `family` is the pod family that makes the calls (the worker's, by default): it is the one the
    organisation's egress rules and the deployment's ceiling are read for."""
    settings = get_settings()
    scope_token: Any = None
    cap = settings.knowledge_max_upload_mib * 1024 * 1024
    async with session_scope(orgs=[org]) as session:
        source = await session.get(KnowledgeSource, source_id)
        if source is None:
            return {"status": "gone"}
        source.status = "syncing"
    summary = _summary()
    to_convert: list[str] = []
    try:
        async with session_scope(orgs=[org]) as session:
            source = await session.get(KnowledgeSource, source_id)
            base = await session.get(KnowledgeBase, source.knowledge_base_id) if source else None
            if source is None or base is None:
                return {"status": "gone"}
            scope_token = await _enter_egress_scope(session, org, family, source)
            adapter = adapter or await open_source(session, source)
            changes = await adapter.changes(source.cursor)
            known = {
                str(d.source_ref): d
                for d in (
                    await session.execute(
                        select(KnowledgeDocument).where(KnowledgeDocument.source_id == source.id)
                    )
                ).scalars()
            }
            count = (
                await session.execute(
                    select(func.count()).where(KnowledgeDocument.knowledge_base_id == base.id)
                )
            ).scalar_one()
            for item in changes.items:
                if item.ref not in known and count >= settings.knowledge_max_documents:
                    _skip(summary, item, "the knowledge base holds its maximum of documents")
                    continue
                converted = await _apply_item(
                    session, source, base, adapter, item, known.get(item.ref), summary, cap
                )
                if converted is not None:
                    to_convert.append(converted)
                    count += item.ref not in known
            gone = await _deleted(changes, known)
            if gone:
                await session.execute(
                    delete(KnowledgeDocument).where(
                        KnowledgeDocument.source_id == source.id,
                        KnowledgeDocument.source_ref.in_(sorted(gone)),
                    )
                )
                summary["deleted"] = len(gone)
            source.cursor = changes.cursor if changes.cursor is not None else source.cursor
        for document_id in to_convert:
            await convert_document(org, document_id)
    except Exception as exc:
        async with session_scope(orgs=[org]) as session:
            failed = await session.get(KnowledgeSource, source_id)
            if failed is not None:
                failed.status, failed.last_error = "failed", redact(f"{type(exc).__name__}: {exc}")[:2000]
        logger.warning(
            "knowledge.source.sync_failed", org=org, source=source_id, error=redact(str(exc))[:300]
        )
        raise
    finally:
        if scope_token is not None:
            pop_scope(scope_token)
        close = getattr(adapter, "close", None)
        if callable(close):
            close()
    async with session_scope(orgs=[org]) as session:
        done = await session.get(KnowledgeSource, source_id)
        if done is not None:
            done.status, done.last_error, done.last_synced_at, done.last_summary = (
                "idle",
                None,
                utcnow(),
                summary,
            )
    logger.info(
        "knowledge.source.synced",
        org=org,
        source=source_id,
        **{k: v for k, v in summary.items() if k != "skipped_reasons"},
    )
    return {"status": "synced", **summary}
