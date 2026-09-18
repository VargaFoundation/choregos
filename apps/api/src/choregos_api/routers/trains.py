"""Trains et releases : état, départ, gel, approbation, abandon.

L'API ne décide rien : elle signale le workflow `ReleaseTrain`, qui est le seul
à savoir si la fenêtre est ouverte et si le lot peut partir.
"""

from __future__ import annotations

from typing import Annotated, Any

from choregos_contracts import EventType
from fastapi import APIRouter, Path, Query, status
from sqlalchemy import select

from ..audit import record
from ..db.models import Project, Release
from ..deps import Db, Me, Pagination, ProjectCtx, resolve_project
from ..errors import forbidden, not_found
from ..rbac import Permission
from ..schemas import (
    AbortRequest,
    ApproveRequest,
    FreezeRequest,
    PageMeta,
    ReleaseDto,
    ReleasePage,
    TrainStatusDto,
)
from ..services import active_policy, persist_event, policy_model, release_dto
from ..temporal import get_temporal, train_id

router = APIRouter(tags=["trains"])

ACTIVE_STATUSES = ["collecting", "departing", "staging", "awaiting_approval", "promoting", "verifying"]


@router.get("/projects/{id}/releases", response_model=ReleasePage, operation_id="listReleases")
async def list_releases(
    ctx: ProjectCtx,
    session: Db,
    page: Pagination,
    env: Annotated[str | None, Query()] = None,
) -> ReleasePage:
    query = select(Release).where(Release.project_id == ctx.id)
    if env:
        query = query.where(Release.env == env)
    rows = (await session.execute(query.order_by(Release.created_at.desc()))).scalars().all()
    window, cursor, has_more = page.slice(list(rows))
    return ReleasePage(
        items=[release_dto(row, ctx.slug) for row in window],
        meta=PageMeta(next_cursor=cursor, has_more=has_more),
    )


async def _release(session: Any, release_id: str, principal: Any) -> tuple[Release, Project, str]:
    row = await session.get(Release, release_id)
    if row is None:
        raise not_found("Release", release_id)
    project = await session.get(Project, row.project_id)
    if project is None:
        raise not_found("Projet", row.project_id)
    _, org_slug = await resolve_project(session, project.id)
    if not principal.can(Permission.PROJECT_READ, org_slug, project.slug):
        raise forbidden()
    return row, project, org_slug


@router.get("/releases/{id}", response_model=ReleaseDto, operation_id="getRelease")
async def get_release(id: str, session: Db, principal: Me) -> ReleaseDto:
    row, project, _ = await _release(session, id, principal)
    return release_dto(row, project.slug)


@router.get("/projects/{id}/trains/{env}", response_model=TrainStatusDto, operation_id="getTrainStatus")
async def train_status(ctx: ProjectCtx, env: Annotated[str, Path()], session: Db) -> TrainStatusDto:
    """État du train : lot en attente, verrou, prochain départ. Source : le workflow, sinon la base."""
    live = await get_temporal().query(train_id(ctx.slug, env), "status")
    current = (
        await session.execute(
            select(Release)
            .where(Release.project_id == ctx.id, Release.env == env, Release.status.in_(ACTIVE_STATUSES))
            .order_by(Release.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    policy = policy_model(await active_policy(session, ctx.id))
    env_policy = policy.release_train.get(env)
    if isinstance(live, dict):
        return TrainStatusDto(
            env=env,
            status=str(live.get("status", "collecting")),
            batch_size=int(live.get("batch_size", 0)),
            pending_items=list(live.get("pending_items", [])),
            next_departure=live.get("next_departure"),
            frozen=bool(live.get("frozen", False)),
            freeze_reason=live.get("freeze_reason"),
            current_release=release_dto(current, ctx.slug) if current else None,
            window_open=bool(live.get("window_open", True)),
        )
    return TrainStatusDto(
        env=env,
        status=current.status if current else "collecting",
        batch_size=len(current.items) if current else 0,
        pending_items=[item.get("work_item_key", "") for item in (current.items if current else [])],
        frozen=bool(env_policy.freeze) if env_policy else False,
        current_release=release_dto(current, ctx.slug) if current else None,
    )


@router.post(
    "/projects/{id}/trains/{env}/depart", status_code=status.HTTP_202_ACCEPTED, operation_id="departTrain"
)
async def depart(ctx: ProjectCtx, env: Annotated[str, Path()], session: Db) -> dict[str, str]:
    ctx.require(Permission.TRAIN_OPERATE)
    await get_temporal().signal(train_id(ctx.slug, env), "depart_now", {"by": ctx.principal.email})
    await record(session, ctx.principal, "train.depart", target_type="train", target_id=f"{ctx.slug}/{env}")
    return {"status": "accepted"}


@router.post(
    "/projects/{id}/trains/{env}/freeze", status_code=status.HTTP_202_ACCEPTED, operation_id="freezeTrain"
)
async def freeze(
    ctx: ProjectCtx, env: Annotated[str, Path()], body: FreezeRequest, session: Db
) -> dict[str, str]:
    """Gel : le motif est obligatoire — un train gelé sans raison est un incident silencieux."""
    ctx.require(Permission.TRAIN_OPERATE)
    await get_temporal().signal(
        train_id(ctx.slug, env), "freeze", {"reason": body.reason, "by": ctx.principal.email}
    )
    await persist_event(
        session,
        EventType.RELEASE_FROZEN,
        project_id=ctx.id,
        project_slug=ctx.slug,
        subject=f"{ctx.slug}/{env}",
        env=env,
        reason=body.reason,
        by=ctx.principal.email,
    )
    await record(
        session,
        ctx.principal,
        "train.freeze",
        target_type="train",
        target_id=f"{ctx.slug}/{env}",
        reason=body.reason,
    )
    return {"status": "accepted"}


@router.post(
    "/projects/{id}/trains/{env}/unfreeze", status_code=status.HTTP_202_ACCEPTED, operation_id="unfreezeTrain"
)
async def unfreeze(ctx: ProjectCtx, env: Annotated[str, Path()], session: Db) -> dict[str, str]:
    ctx.require(Permission.TRAIN_OPERATE)
    await get_temporal().signal(train_id(ctx.slug, env), "unfreeze", {"by": ctx.principal.email})
    await record(session, ctx.principal, "train.unfreeze", target_type="train", target_id=f"{ctx.slug}/{env}")
    return {"status": "accepted"}


@router.post("/releases/{id}/approve", status_code=status.HTTP_202_ACCEPTED, operation_id="approveRelease")
async def approve(id: str, body: ApproveRequest, session: Db, principal: Me) -> dict[str, str]:
    """Approuver une mise en production demande le rôle release_captain (ou owner)."""
    row, project, org_slug = await _release(session, id, principal)
    if not principal.can(Permission.TRAIN_APPROVE, org_slug, project.slug):
        raise forbidden("approuver une release demande le rôle release_captain")
    await get_temporal().signal(
        train_id(project.slug, row.env),
        "approve",
        {"release_id": row.id, "by": principal.email, "note": body.note},
    )
    row.approved_by = principal.email
    await record(session, principal, "release.approve", target_type="release", target_id=row.id)
    return {"status": "accepted"}


@router.post("/releases/{id}/abort", status_code=status.HTTP_202_ACCEPTED, operation_id="abortRelease")
async def abort(id: str, body: AbortRequest, session: Db, principal: Me) -> dict[str, str]:
    row, project, org_slug = await _release(session, id, principal)
    if not principal.can(Permission.TRAIN_OPERATE, org_slug, project.slug):
        raise forbidden()
    await get_temporal().signal(
        train_id(project.slug, row.env),
        "abort",
        {"release_id": row.id, "by": principal.email, "reason": body.reason},
    )
    await record(
        session, principal, "release.abort", target_type="release", target_id=row.id, reason=body.reason
    )
    return {"status": "accepted"}
