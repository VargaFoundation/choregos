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

from ..audit import record
from ..db.models import Agent, AgentVersion, BudgetAlert, CostLedger, Project, Run
from ..schemas import AgentSpec
from .agents import depense_du_jour, depense_du_mois

#: The thresholds, in the order they are crossed: the policy's `alert_at_ratio` (0.8 by default), then
#: the budget itself.
FULL = 1.0
#: An agent's monthly cap alerts at half, at 80 %, and at the cap (S27-09).
MONTH_RATIOS = (0.5, 0.8, FULL)


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
    period: str = "day",
) -> BudgetAlert | None:
    """The alert for this threshold and day, once. `None` when it already exists."""
    existing = await session.execute(
        select(BudgetAlert.id).where(
            BudgetAlert.org_id == org_id,
            BudgetAlert.scope == scope,
            BudgetAlert.subject == subject,
            BudgetAlert.period == period,
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
        period=period,
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
        raised += await _check_agent_month(session, project.org_id, run.agent_slug, spec_row)
    return raised


async def _check_agent_month(
    session: AsyncSession, org_id: str, slug: str, spec_row: Any
) -> list[BudgetAlert]:
    """The agent's monthly cap: an alert at 50, 80 and 100 %, once each per month. At 100 % the
    agent is suspended: its next runs are refused, until a person lifts the suspension (and, for the
    rest of the month, raises the cap)."""
    cap = AgentSpec.model_validate(spec_row or {}).budget.monthly_usd
    if not cap:
        return []
    spent = await depense_du_mois(session, org_id, slug)
    month = utcnow().strftime("%Y-%m")
    raised: list[BudgetAlert] = []
    reached = crossed(spent, cap, MONTH_RATIOS)
    for percent in reached:
        alert = await _raise(
            session,
            org_id=org_id,
            scope="agent",
            subject=slug,
            day=month,
            percent=percent,
            spent=spent,
            budget=float(cap),
            period="month",
        )
        if alert is not None:
            raised.append(alert)
    if 100 in reached:
        await suspend_for_budget(session, org_id, slug, spent=spent, cap=float(cap))
    return raised


async def suspend_for_budget(
    session: AsyncSession, org_id: str, slug: str, *, spent: float, cap: float
) -> bool:
    """The hard stop: the agent is `suspended`, and the audit log says why. `False` when it already
    was (or is revoked): nothing to do twice."""
    agent = (
        await session.execute(select(Agent).where(Agent.org_id == org_id, Agent.slug == slug))
    ).scalar_one_or_none()
    if agent is None or agent.status != "active":
        return False
    agent.status = "suspended"
    await record(
        session,
        None,
        "agent.budget_suspend",
        org_id=org_id,
        target_type="agent",
        target_id=agent.id,
        spent_usd=round(spent, 6),
        monthly_usd=cap,
    )
    return True


async def unsent_month_alerts(session: AsyncSession, org_id: str) -> list[BudgetAlert]:
    """The monthly agent alerts that have not reached Slack yet, oldest first."""
    rows = await session.execute(
        select(BudgetAlert)
        .where(
            BudgetAlert.org_id == org_id,
            BudgetAlert.scope == "agent",
            BudgetAlert.period == "month",
            BudgetAlert.notified_at.is_(None),
        )
        .order_by(BudgetAlert.threshold)
    )
    return list(rows.scalars())


__all__ = [
    "check_after_spend",
    "crossed",
    "suspend_for_budget",
    "thresholds",
    "unsent_month_alerts",
]
