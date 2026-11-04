# SPDX-License-Identifier: Apache-2.0
"""Run history and metrics of an agent."""

from __future__ import annotations

from typing import Annotated

from choregos_core import utcnow
from fastapi import Query
from sqlalchemy import func, select

from ...db.models import (
    Agent,
)
from ...deps import Db, Me
from ...rbac import Permission
from ...schemas import (
    AgentMetrics,
    AgentMetricsSummary,
    AgentProjectMetrics,
    AgentSpec,
    AgentVersionMetrics,
    RunDto,
)
from ...services.agent_access import (
    load_visible_agent,
    require_agent_reader,
)
from ...services.agent_governance import (
    visible_clause,
)
from ...services.agent_releases import (
    production_spec,
)
from ...services.agents import (
    depense_du_jour,
)
from ...services.organisations import organisation_by_slug
from ._router import Slug, router

FINIS = {"succeeded", "failed", "timed_out", "cancelled"}


@router.get("/orgs/{org}/agents/{slug}/metrics", response_model=AgentMetrics, operation_id="getAgentMetrics")
async def agent_metrics(
    org: str, slug: Slug, session: Db, principal: Me, days: Annotated[int, Query(ge=1, le=365)] = 30
) -> AgentMetrics:
    """The agent's runs over the period, their outcome, their cost — models and tools —, per project."""
    from datetime import timedelta

    from ...db.models import CostLedger, Project, Run

    organisation, agent, _ = await load_visible_agent(session, principal, org, slug)
    window_start = utcnow() - timedelta(days=days)
    runs = (
        await session.execute(
            select(Run.id, Run.status, Project.slug, Run.agent_version)
            .join(Project, Project.id == Run.project_id)
            .where(Run.agent_slug == slug, Project.org_id == organisation.id, Run.created_at >= window_start)
        )
    ).all()
    cost_by_version = (
        await session.execute(
            select(Run.agent_version, func.sum(CostLedger.cost_usd))
            .join(Run, Run.id == CostLedger.run_id)
            .join(Project, Project.id == Run.project_id)
            .where(Run.agent_slug == slug, Project.org_id == organisation.id, CostLedger.ts >= window_start)
            .group_by(Run.agent_version)
        )
    ).all()
    cost_rows = (
        await session.execute(
            select(CostLedger.kind, Project.slug, func.sum(CostLedger.cost_usd))
            .join(Run, Run.id == CostLedger.run_id)
            .join(Project, Project.id == Run.project_id)
            .where(Run.agent_slug == slug, Project.org_id == organisation.id, CostLedger.ts >= window_start)
            .group_by(CostLedger.kind, Project.slug)
        )
    ).all()
    succeeded_count = sum(1 for _, run_status, _, _ in runs if run_status == "succeeded")
    finished_count = sum(1 for _, run_status, _, _ in runs if run_status in FINIS)
    cost_by_kind: dict[str, float] = {}
    by_project: dict[str, list[float]] = {}
    for cost_kind, project_key, amount_usd in cost_rows:
        cost_by_kind[cost_kind] = cost_by_kind.get(cost_kind, 0.0) + float(amount_usd or 0.0)
        by_project.setdefault(project_key, [0, 0.0])[1] += float(amount_usd or 0.0)
    for _, _, project_key, _ in runs:
        by_project.setdefault(project_key, [0, 0.0])[0] += 1
    by_version: dict[int, list[int]] = {}
    for _, run_status, _, version_number in runs:
        if version_number is None:
            continue
        version_counts = by_version.setdefault(int(version_number), [0, 0, 0])
        if run_status in FINIS:
            version_counts[0] += 1
            version_counts[1] += 1 if run_status == "succeeded" else 0
        version_counts[2] += 1
    version_cost = {int(v): float(c or 0.0) for v, c in cost_by_version if v is not None}
    # The budget shown is production's: it is the revision most runs use (ADR 0071).
    production_rev = await production_spec(session, agent)
    return AgentMetrics(
        agent=slug,
        days=days,
        runs=len(runs),
        succeeded=succeeded_count,
        failed=finished_count - succeeded_count,
        success_rate=round(succeeded_count / finished_count, 4) if finished_count else None,
        cost_usd=round(sum(cost_by_kind.values()), 6),
        cost_by_kind={k: round(v, 6) for k, v in cost_by_kind.items()},
        by_project=[
            AgentProjectMetrics(project=project_key, runs=int(n), cost_usd=round(c, 6))
            for project_key, (n, c) in sorted(by_project.items())
        ],
        spent_today_usd=round(await depense_du_jour(session, organisation.id, slug), 6),
        daily_budget_usd=AgentSpec.model_validate(production_rev or {}).budget.daily_usd,
        by_version=[
            AgentVersionMetrics(
                version=version_number,
                runs=total_v,
                succeeded=reussis_v,
                failed=finis_v - reussis_v,
                success_rate=round(reussis_v / finis_v, 4) if finis_v else None,
                cost_usd=round(version_cost.get(version_number, 0.0), 6),
            )
            for version_number, (finis_v, reussis_v, total_v) in sorted(by_version.items())
        ],
    )


@router.get(
    "/orgs/{org}/agent-metrics", response_model=list[AgentMetricsSummary], operation_id="listAgentMetrics"
)
async def list_agent_metrics(
    org: str, session: Db, principal: Me, days: Annotated[int, Query(ge=1, le=365)] = 30
) -> list[AgentMetricsSummary]:
    """The registry's columns for every agent at once (S30-04): runs, success rate, cost — two
    grouped queries instead of one call per agent."""
    from datetime import timedelta

    from ...db.models import CostLedger, Project, Run

    require_agent_reader(principal, org)
    organisation = await organisation_by_slug(session, org)
    window_start = utcnow() - timedelta(days=days)
    slugs = list(
        (
            await session.execute(
                select(Agent.slug)
                .where(
                    Agent.org_id == organisation.id, await visible_clause(session, principal, organisation)
                )
                .order_by(Agent.slug)
            )
        ).scalars()
    )
    runs = (
        await session.execute(
            select(Run.agent_slug, Run.status, func.count())
            .join(Project, Project.id == Run.project_id)
            .where(
                Run.agent_slug.in_(slugs), Project.org_id == organisation.id, Run.created_at >= window_start
            )
            .group_by(Run.agent_slug, Run.status)
        )
    ).all()
    cost_rows = (
        await session.execute(
            select(Run.agent_slug, func.sum(CostLedger.cost_usd))
            .join(Run, Run.id == CostLedger.run_id)
            .join(Project, Project.id == Run.project_id)
            .where(
                Run.agent_slug.in_(slugs), Project.org_id == organisation.id, CostLedger.ts >= window_start
            )
            .group_by(Run.agent_slug)
        )
    ).all()
    total: dict[str, int] = {}
    succeeded_count: dict[str, int] = {}
    finished_count: dict[str, int] = {}
    for slug_read, run_status, run_count in runs:
        agent_key = str(slug_read)
        total[agent_key] = total.get(agent_key, 0) + int(run_count)
        if run_status in FINIS:
            finished_count[agent_key] = finished_count.get(agent_key, 0) + int(run_count)
        if run_status == "succeeded":
            succeeded_count[agent_key] = succeeded_count.get(agent_key, 0) + int(run_count)
    cost_by_agent = {str(slug_read): float(amount_usd or 0.0) for slug_read, amount_usd in cost_rows}
    return [
        AgentMetricsSummary(
            agent=slug,
            days=days,
            runs=total.get(slug, 0),
            success_rate=round(succeeded_count.get(slug, 0) / finished_count[slug], 4)
            if finished_count.get(slug)
            else None,
            cost_usd=round(cost_by_agent.get(slug, 0.0), 6),
        )
        for slug in slugs
    ]


@router.get("/orgs/{org}/agents/{slug}/runs", response_model=list[RunDto], operation_id="listAgentRuns")
async def list_agent_runs(
    org: str, slug: Slug, session: Db, principal: Me, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[RunDto]:
    """The agent's latest runs (S30-05): its page lists them in a tab, newest first, from the
    projects the caller may read only."""
    from ...db.models import Project, Run
    from ...services.tickets import run_dto

    organisation, _, _ = await load_visible_agent(session, principal, org, slug)
    db_rows = (
        await session.execute(
            select(Run, Project.slug)
            .join(Project, Project.id == Run.project_id)
            .where(Run.agent_slug == slug, Project.org_id == organisation.id)
            .order_by(Run.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        run_dto(run, project_key)
        for run, project_key in db_rows
        if principal.can(Permission.PROJECT_READ, org, project_key)
    ]
