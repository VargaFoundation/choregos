# SPDX-License-Identifier: Apache-2.0
"""Les actions gouvernées d'un projet (ADR 0035) : proposer, lire, décider.

L'exécution n'est jamais ici : une action approuvée part dans Temporal (`ActionWorkflow`), et la
réponse quitte l'API avant le moindre effet. Le journal de ses effets se lit avec elle.
"""

from __future__ import annotations

from typing import Annotated

from choregos_contracts import ActionOrigin
from fastapi import APIRouter, Path, Query, status
from sqlalchemy import select

from ..db.models import Action
from ..deps import Db, ProjectCtx
from ..errors import not_found
from ..rbac import Permission
from ..schemas.actions import ActionCreate, ActionDecisionBody, ActionDto
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
    """Une action en attente de décision. Ses effets doivent être déclarés (`declarer_un_effet`)."""
    ctx.require(Permission.ITEM_CONTROL)
    action = await service.proposer(
        session,
        ctx.project,
        body,
        origine=ActionOrigin.TRANSITION.value if body.work_item_id else ActionOrigin.TOOL.value,
        propose_par={"kind": ctx.principal.kind, "id": ctx.principal.email, "via": "api"},
        principal=ctx.principal,
    )
    return await service.dto(session, action)


@router.get("/projects/{id}/actions", response_model=list[ActionDto], operation_id="listActions")
async def list_actions(
    ctx: ProjectCtx, session: Db, status_: Annotated[str | None, Query(alias="status")] = None
) -> list[ActionDto]:
    requete = select(Action).where(Action.project_id == ctx.id).order_by(Action.created_at.desc()).limit(200)
    if status_:
        requete = requete.where(Action.status == status_)
    return [await service.dto(session, a) for a in (await session.execute(requete)).scalars()]


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
    """Approuver (session récente exigée) ou rejeter (avec une raison). Une approbation suffisante
    démarre `action-<id>` dans Temporal ; rien ne s'exécute dans cette requête."""
    action = await service.decider(
        session, ctx.project, ctx.org_slug, action_id, body.decision, body.reason, ctx.principal
    )
    return await service.dto(session, action)
