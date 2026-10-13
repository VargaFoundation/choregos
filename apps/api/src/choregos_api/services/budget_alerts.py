# SPDX-License-Identifier: Apache-2.0
"""Budget thresholds, and the inbox items they raise (S29-04).

`crossed(spent, budget, ratios)` is the one place that says which thresholds a spend has crossed:
S27-09 (alerts at 50/80/100 % to Slack) should call it rather than redo the comparison, so the
inbox and Slack never disagree on what "80 %" means.

`check_after_spend` runs after a cost is written. For the run's project (its daily budget, in the
policy) and the run's agent (its daily budget, in its version), it raises ONE alert per threshold
and day: the unique key `(org, scope, subject, period, day, threshold)` is the idempotence.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Any

from choregos_core import utcnow
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Agent, AgentVersion, BudgetAlert, CostLedger, Project, Run
from ..schemas import AgentSpec
from .agents import depense_du_jour

#: The thresholds, in the order they are crossed: the policy's `alert_at_ratio` (0.8 by default), then
#: the budget itself.
FULL = 1.0


def thresholds(alert_at_ratio: float | None) -> tuple[float, ...]:
    ratios = {FULL}
    if alert_at_ratio and 0 < alert_at_ratio < FULL:
        ratios.add(alert_at_ratio)
    return tuple(sorted(ratios))


def crossed(spent: float, budget: float | None, ratios: tuple[float, ...]) -> list[int]:
    """The thresholds (in percent) that `spent` has reached on `budget`, lowest first. No budget, or
    a budget of zero, crosses nothing: there is nothing to be at 80 % of."""
    if not budget or budget <= 0:
        return []
    return [round(ratio * 100) for ratio in ratios if spent >= ratio * budget]


async def _raise(
    session: AsyncSession,
    *,
    org_id: str,
    scope: str,
    subject: str,
    day: str,
    percent: int,
    spent: float,
    budget: float,
) -> BudgetAlert | None:
    """The alert for this threshold and day, once. `None` when it already exists."""
    existing = await session.execute(
        select(BudgetAlert.id).where(
            BudgetAlert.org_id == org_id,
            BudgetAlert.scope == scope,
            BudgetAlert.subject == subject,
            BudgetAlert.period == "day",
            BudgetAlert.period_key == day,
            BudgetAlert.threshold == percent,
        )
    )
    if existing.first() is not None:
        return None
    alert = BudgetAlert(
        org_id=org_id,
        scope=scope,
        subject=subject,
        period="day",
        period_key=day,
        threshold=percent,
        spent_usd=round(spent, 6),
        budget_usd=round(budget, 6),
        status="open",
    )
    try:
        async with session.begin_nested():
            session.add(alert)
            await session.flush()
    except IntegrityError:
        return None  # a concurrent writer raised it first
    return alert


async def check_after_spend(session: AsyncSession, run_id: str | None) -> list[BudgetAlert]:
    """Raises the alerts a run's spend has just made due; the new ones are returned."""
    from .definitions import active_policy, policy_model

    run = await session.get(Run, run_id) if run_id else None
    project = await session.get(Project, run.project_id) if run else None
    if run is None or project is None:
        return []
    await session.flush()  # the cost just recorded is part of the sum
    midnight = datetime.combine(utcnow().date(), time.min, tzinfo=UTC)
    day = utcnow().date().isoformat()
    raised: list[BudgetAlert] = []

    policy = policy_model(await active_policy(session, project.id)).budgets
    spent_project = float(
        (
            await session.execute(
                select(func.coalesce(func.sum(CostLedger.cost_usd), 0.0)).where(
                    CostLedger.project_id == project.id, CostLedger.ts >= midnight
                )
            )
        ).scalar_one()
    )
    for percent in crossed(spent_project, policy.daily_project_usd, thresholds(policy.alert_at_ratio)):
        alert = await _raise(
            session,
            org_id=project.org_id,
            scope="project",
            subject=project.slug,
            day=day,
            percent=percent,
            spent=spent_project,
            budget=float(policy.daily_project_usd or 0),
        )
        if alert is not None:
            raised.append(alert)

    if run.agent_slug:
        spec_row: Any = (
            await session.execute(
                select(AgentVersion.spec)
                .join(Agent, Agent.id == AgentVersion.agent_id)
                .where(Agent.org_id == project.org_id, Agent.slug == run.agent_slug)
                .order_by(AgentVersion.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        budget = AgentSpec.model_validate(spec_row or {}).budget.daily_usd
        spent_agent = await depense_du_jour(session, project.org_id, run.agent_slug)
        for percent in crossed(spent_agent, budget, thresholds(policy.alert_at_ratio)):
            alert = await _raise(
                session,
                org_id=project.org_id,
                scope="agent",
                subject=run.agent_slug,
                day=day,
                percent=percent,
                spent=spent_agent,
                budget=float(budget or 0),
            )
            if alert is not None:
                raised.append(alert)
    return raised


__all__ = ["check_after_spend", "crossed", "thresholds"]
