# SPDX-License-Identifier: Apache-2.0
"""A project's governed actions (ADR 0035): propose, read, decide, and settle one in doubt.

Execution never happens here: an approved action goes to Temporal (`ActionWorkflow`), and the
response leaves the API before any effect. The log of its effects is read with it.
"""

from __future__ import annotations

from typing import Annotated

from choregos_contracts import ActionOrigin
from fastapi import APIRouter, Path, Query, status
from sqlalchemy import select

from ..db.models import Action
from ..deps import Db, Me, ProjectCtx
from ..errors import not_found
from ..rbac import Permission
from ..schemas.actions import ActionCreate, ActionDecisionBody, ActionDto, ActionSettlementBody
from ..services import actions as service

router = APIRouter(tags=["actions"])

Ident = Annotated[str, Path(min_length=1, max_length=64)]


@router.post(
    "/projects/{id}/actions",
    response_model=ActionDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="proposeAction",
)
async def propose_action(ctx: ProjectCtx, body: ActionCreate, session: Db) -> ActionDto:
    """An action awaiting a decision. Its effects must be declared (`declare_effect`)."""
    ctx.require(Permission.ITEM_CONTROL)
    action = await service.propose(
        session,
        ctx.project,
        body,
        origin=ActionOrigin.TRANSITION.value if body.work_item_id else ActionOrigin.TOOL.value,
        proposed_by={"kind": ctx.principal.kind, "id": ctx.principal.email, "via": "api"},
        principal=ctx.principal,
    )
    return await service.dto(session, action)


@router.get("/projects/{id}/actions", response_model=list[ActionDto], operation_id="listActions")
async def list_actions(
    ctx: ProjectCtx, session: Db, status_: Annotated[str | None, Query(alias="status")] = None
) -> list[ActionDto]:
    query = select(Action).where(Action.project_id == ctx.id).order_by(Action.created_at.desc()).limit(200)
    if status_:
        query = query.where(Action.status == status_)
    return [await service.dto(session, a) for a in (await session.execute(query)).scalars()]


@router.get("/orgs/{org}/actions", response_model=list[ActionDto], operation_id="listOrgActions")
async def list_org_actions(
    org: str, session: Db, principal: Me, status_: Annotated[str | None, Query(alias="status")] = None
) -> list[ActionDto]:
    """The decision inbox: the actions of the organisation's projects that the caller can see;
    `status=pending_approval` for what is waiting on someone."""
    from ..db.models import Organization, Project

    visible = [
        p
        for p in (
            await session.execute(
                select(Project)
                .join(Organization, Organization.id == Project.org_id)
                .where(Organization.slug == org)
            )
        ).scalars()
        if principal.can(Permission.PROJECT_READ, org, p.slug)
    ]
    if not visible:
        return []
    query = (
        select(Action)
        .where(Action.project_id.in_([p.id for p in visible]))
        .order_by(Action.created_at.desc())
        .limit(200)
    )
    if status_:
        query = query.where(Action.status == status_)
    return [await service.dto(session, a) for a in (await session.execute(query)).scalars()]


@router.get("/projects/{id}/actions/{action_id}", response_model=ActionDto, operation_id="getAction")
async def get_action(ctx: ProjectCtx, action_id: Ident, session: Db) -> ActionDto:
    action = (
        await session.execute(select(Action).where(Action.id == action_id, Action.project_id == ctx.id))
    ).scalar_one_or_none()
    if action is None:
        raise not_found("Action", action_id)
    return await service.dto(session, action)


@router.post(
    "/projects/{id}/actions/{action_id}/decision", response_model=ActionDto, operation_id="decideAction"
)
async def decide_action(
    ctx: ProjectCtx, action_id: Ident, body: ActionDecisionBody, session: Db
) -> ActionDto:
    """Approve (a recent session is required) or reject (with a reason). A sufficient approval
    starts `action-<id>` in Temporal; nothing runs inside this request."""
    action = await service.decide(
        session, ctx.project, ctx.org_slug, action_id, body.decision, body.reason, ctx.principal
    )
    return await service.dto(session, action)


@router.post(
    "/projects/{id}/actions/{action_id}/settlement", response_model=ActionDto, operation_id="settleAction"
)
async def settle_action(
    ctx: ProjectCtx, action_id: Ident, body: ActionSettlementBody, session: Db
) -> ActionDto:
    """A person records what they found of an action in doubt (ADR 0149 §5, S65-26): the write was
    `made` (the action resumes after it) or `not_made` (it fails and compensates what came before).
    Guarded as an approval is; the workflow leaves after the commit, nothing runs within this request."""
    action = await service.settle(session, ctx.project, ctx.org_slug, action_id, body, ctx.principal)
    return await service.dto(session, action)
