# SPDX-License-Identifier: Apache-2.0
"""Knowledge sources: create, patch, delete and sync (S33-04)."""

from __future__ import annotations

import contextlib

from fastapi import status
from sqlalchemy import delete, select

from ...audit import record
from ...db.models import (
    KnowledgeDocument,
    KnowledgeSource,
)
from ...deps import Db, Me
from ...knowledge import refresh
from ...knowledge.access import (
    KnowledgeAction,
)
from ...knowledge.sync import CONNECTED_KINDS, OPTIONALLY_CONNECTED
from ...schemas.knowledge import (
    KnowledgeBaseDto,
    KnowledgeSourceCreate,
    KnowledgeSourceDto,
    KnowledgeSourcePatch,
)
from ...services.organisations import organisation_by_slug
from ...temporal import get_temporal, knowledge_sync_id
from ._common import _base, _dto, _require, _source_dto
from ._router import Slug, router
from .source_config import (
    _checked_refresh,
    _checked_source_config,
    _egress_gate,
    _reachable_start,
    _source,
    _start_sync_loop,
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
    organisation = await organisation_by_slug(session, org)
    config = await _checked_source_config(session, organisation, body.kind, body.connector, body.config)
    await _egress_gate(
        session, principal, organisation, body.kind, body.connector, config, body.name, body.allow_egress
    )
    await _reachable_start(session, organisation, body.kind, config, body.connector)
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
        organisation = await organisation_by_slug(session, org)
        previous = dict(source.config or {})
        source.config = await _checked_source_config(
            session, organisation, source.kind, source.connector, body.config
        )
        if previous.get("github_app") and previous.get("url") == source.config.get("url"):
            # A project's own repository a template attached (S51-06): read through the App while
            # the URL stays; no request can grant that flag.
            source.config = {**source.config, "github_app": True}
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
        await _reachable_start(session, organisation, source.kind, source.config, source.connector)
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
