# SPDX-License-Identifier: Apache-2.0
"""Projection d'un projet : son DTO et ses statistiques du mois."""

from __future__ import annotations

from choregos_core import (
    utcnow,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    CostLedger,
    Organization,
    Project,
    Release,
    Run,
    WorkflowDef,
    WorkItem,
)
from ..schemas import (
    ProjectDto,
    ProjectStats,
)
from .definitions import active_policy, active_workflow


async def project_dto(session: AsyncSession, project: Project, org_slug: str | None = None) -> ProjectDto:
    if org_slug is None:
        org = await session.get(Organization, project.org_id)
        org_slug = org.slug if org else ""
    workflow = await active_workflow(session, project.id)
    policy = await active_policy(session, project.id)
    return ProjectDto(
        id=project.id,
        slug=project.slug,
        org=org_slug,
        name=project.name,
        template_ref=project.template_ref,
        status=project.status,
        config=project.config,
        workflow_name=workflow.name if workflow else None,
        policy_name=policy.name if policy else None,
        has_release_train=await declares_a_release_train(session, project.id),
        stats=await project_stats(session, project),
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


async def declares_a_release_train(session: AsyncSession, project_id: str) -> bool:
    """Whether an active workflow of the project has a `via: release_train` transition: the engine
    starts a train only for such a transition, so a project without one has no train (S30-07)."""
    documents = (
        await session.execute(
            select(WorkflowDef.json_doc).where(
                WorkflowDef.project_id == project_id, WorkflowDef.is_active.is_(True)
            )
        )
    ).scalars()
    return any(
        transition.get("via") == "release_train"
        for document in documents
        for transition in (document or {}).get("transitions") or []
    )


async def project_stats(session: AsyncSession, project: Project) -> ProjectStats:
    since = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    active = (
        await session.execute(
            select(func.count())
            .select_from(WorkItem)
            .where(WorkItem.project_id == project.id, WorkItem.closed_at.is_(None))
        )
    ).scalar_one()
    cost = (
        await session.execute(
            select(func.coalesce(func.sum(CostLedger.cost_eur), 0.0)).where(
                CostLedger.project_id == project.id, CostLedger.ts >= since
            )
        )
    ).scalar_one()
    trains = (
        await session.execute(
            select(func.count())
            .select_from(Release)
            .where(
                Release.project_id == project.id,
                Release.status.in_(["collecting", "departing", "staging", "awaiting_approval", "promoting"]),
            )
        )
    ).scalar_one()
    return ProjectStats(
        active_work_items=int(active),
        cost_month_eur=round(float(cost), 4),
        trains_pending=int(trains),
        first_pass_merge_rate=await first_pass_merge_rate(session, project.id),
    )


async def first_pass_merge_rate(session: AsyncSession, project_id: str) -> float | None:
    """Part des tickets fermés dont l'implémentation n'a demandé qu'une tentative."""
    items = (
        (
            await session.execute(
                select(WorkItem.id).where(WorkItem.project_id == project_id, WorkItem.closed_at.is_not(None))
            )
        )
        .scalars()
        .all()
    )
    if not items:
        return None
    good = 0
    for item_id in items:
        attempts = (
            await session.execute(
                select(func.max(Run.attempt)).where(
                    Run.work_item_id == item_id, Run.stage_role == "implement"
                )
            )
        ).scalar_one()
        if attempts is None or attempts <= 1:
            good += 1
    return round(good / len(items), 4)
