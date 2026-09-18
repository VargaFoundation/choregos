"""Coûts : agrégats par jour, étape, modèle, backend, taille — et par organisation."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Annotated, Any

from choregos_core import utcnow
from fastapi import APIRouter, Query
from sqlalchemy import func, select

from ..db.models import CostLedger, Organization, Project
from ..deps import Db, Me, ProjectCtx
from ..errors import forbidden, not_found
from ..rbac import Permission
from ..schemas import CostReport, CostRow, CostTotal
from ..services import active_policy, policy_model

router = APIRouter(tags=["costs"])

GROUPS: dict[str, Any] = {
    "stage": CostLedger.stage_role,
    "model": CostLedger.model,
    "backend": CostLedger.backend,
    "size": CostLedger.size,
    "project": CostLedger.project_id,
}


def _default_since() -> datetime:
    return utcnow() - timedelta(days=30)


async def _report(
    session: Db,
    *,
    where: list[Any],
    group_by: str,
    budget_usd: float | None = None,
) -> CostReport:
    column: Any = GROUPS.get(group_by, CostLedger.project_id)
    if group_by == "day":
        column = func.date(CostLedger.ts)
    query = (
        select(
            column.label("key"),
            func.sum(CostLedger.cost_usd),
            func.sum(CostLedger.cost_eur),
            func.sum(CostLedger.tokens_in),
            func.sum(CostLedger.tokens_out),
            func.sum(CostLedger.tokens_cached),
            func.count(CostLedger.id),
        )
        .where(*where)
        .group_by(column)
        .order_by(column)
    )
    rows = (await session.execute(query)).all()
    report_rows = [
        CostRow(
            key=str(row[0] if row[0] is not None else "—"),
            cost_usd=round(float(row[1] or 0), 6),
            cost_eur=round(float(row[2] or 0), 6),
            tokens_in=int(row[3] or 0),
            tokens_out=int(row[4] or 0),
            tokens_cached=int(row[5] or 0),
            runs=int(row[6] or 0),
        )
        for row in rows
    ]
    return CostReport(
        group_by=group_by,
        rows=report_rows,
        total=CostTotal(
            cost_usd=round(sum(r.cost_usd for r in report_rows), 6),
            cost_eur=round(sum(r.cost_eur for r in report_rows), 6),
            budget_usd=budget_usd,
        ),
    )


@router.get("/projects/{id}/costs", response_model=CostReport, operation_id="getProjectCosts")
async def project_costs(
    ctx: ProjectCtx,
    session: Db,
    group_by: Annotated[str, Query(pattern="^(day|stage|model|backend|size)$")] = "day",
    since: Annotated[date | None, Query()] = None,
    until: Annotated[date | None, Query()] = None,
) -> CostReport:
    where: list[Any] = [CostLedger.project_id == ctx.id]
    where.append(
        CostLedger.ts >= (datetime.combine(since, datetime.min.time()) if since else _default_since())
    )
    if until:
        where.append(CostLedger.ts <= datetime.combine(until, datetime.max.time()))
    policy = policy_model(await active_policy(session, ctx.id))
    return await _report(session, where=where, group_by=group_by, budget_usd=policy.budgets.daily_project_usd)


@router.get("/orgs/{org}/costs", response_model=CostReport, operation_id="getOrgCosts")
async def org_costs(
    org: str,
    session: Db,
    principal: Me,
    group_by: Annotated[str, Query(pattern="^(day|project|model)$")] = "day",
    since: Annotated[date | None, Query()] = None,
) -> CostReport:
    organization = (
        await session.execute(select(Organization).where(Organization.slug == org))
    ).scalar_one_or_none()
    if organization is None:
        raise not_found("Organisation", org)
    if not principal.can(Permission.PROJECT_READ, org):
        raise forbidden()
    project_ids = (
        (await session.execute(select(Project.id).where(Project.org_id == organization.id))).scalars().all()
    )
    where: list[Any] = [CostLedger.project_id.in_(list(project_ids) or [""])]
    where.append(
        CostLedger.ts >= (datetime.combine(since, datetime.min.time()) if since else _default_since())
    )
    report = await _report(session, where=where, group_by=group_by)
    if group_by == "project":
        slug_rows = (
            await session.execute(select(Project.id, Project.slug).where(Project.org_id == organization.id))
        ).all()
        slugs: dict[str, str] = {str(row[0]): str(row[1]) for row in slug_rows}
        for row in report.rows:
            row.key = slugs.get(row.key, row.key)
    return report
