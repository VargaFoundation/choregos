# SPDX-License-Identifier: Apache-2.0
"""Distilling a knowledge base: estimate, start, relaunch and stop (ADR 0082, ADR 0092)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Query, status

from ...audit import record
from ...deps import Db, Me
from ...errors import ApiError, conflict
from ...knowledge import refresh
from ...knowledge.access import (
    KnowledgeAction,
)
from ...logging import get_logger
from ...schemas.knowledge import (
    KnowledgeBaseDto,
    KnowledgeDistillEstimate,
    KnowledgeDistillRequest,
)
from ...temporal import get_temporal, knowledge_distill_id
from ._common import _base, _dto, _require
from ._router import Slug, router

log = get_logger("choregos.knowledge.router")


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
    from ...knowledge.distill import estimate_for

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
    from ...knowledge.distill import estimate_for, estimated_total, is_running, new_run

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
    from ...knowledge.distill import is_running, stopped_record, write_if_running

    base = await _base(session, principal, org, slug)
    _require(principal, org, base, KnowledgeAction.DISTILL)
    before = base.distillation
    # Written only if the run is still running: a run that published meanwhile is not called stopped.
    if (
        not before
        or not is_running(before)
        or not await write_if_running(
            session, base.id, before.get("started_at"), stopped_record(before, principal.email)
        )
    ):
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "Conflict",
            f"knowledge base `{slug}` is not being distilled",
            extra={"code": "distill_not_running"},
        )
    await session.refresh(base)
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
