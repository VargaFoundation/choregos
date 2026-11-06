# SPDX-License-Identifier: Apache-2.0
"""The refusals that stop a stage before it starts: model guard, admission, budgets and caps.

Helpers of `stage`, which re-exports them; none of them is a Temporal activity.
"""

from __future__ import annotations

from typing import Any

import structlog
from choregos_api.db.models import Run
from choregos_api.services import persist_event
from choregos_contracts import (
    EventType,
)
from sqlalchemy import select

from .plan import StagePlan

logger = structlog.get_logger("choregos.stage")


def _require_llm_guard(bundle: Any) -> None:
    """A policy that masks secrets on the LLM path needs a gateway that can (ADR 0084): refused
    before the budget is reserved, and escalated like a model the project cannot use — the
    configuration, not the run, has to change."""
    from choregos_adapters.base import llm_guard_refusal
    from temporalio.exceptions import ApplicationError

    if not getattr(bundle.policy.sandbox, "llm_security_analyzer", False):
        return
    if (refusal := llm_guard_refusal(bundle.adapters.gateway)) is not None:
        raise ApplicationError(refusal, type="ModelResolutionError", non_retryable=True)


async def _admettre(
    bundle: Any, item: Any, run_id: str, plan: StagePlan, backend: str, budget_usd: float
) -> None:
    """The admissions declared by the extensions (`choregos_core.admission`), before any key.

    A refusal stops the step WITHOUT a retry, like the guard against injection: retrying would
    change nothing as long as the rule holds, and the work item is marked dead with the reason,
    which a human reads.
    """
    from choregos_core.admission import AdmissionRefused, RunRequest, check
    from temporalio.exceptions import ApplicationError

    admission_request = RunRequest(
        org=bundle.org_slug,
        project=bundle.slug,
        work_item=item.tracker_key,
        run_id=run_id,
        role=plan.role,
        backend=backend,
        budget_usd=budget_usd,
    )
    try:
        await check(admission_request)
    except AdmissionRefused as refusal:
        raise ApplicationError(
            f"run refused before it started — {refusal}", type="admission_refused", non_retryable=True
        ) from refusal


async def _reservation(
    session: Any,
    bundle: Any,
    item: Any,
    run_id: str,
    agent: Any,
    stage_usd: float,
    *,
    trial: bool = False,
) -> Any:
    """What this run may spend (ADR 0047): the smallest of what is left under the stage budget, the
    agent's run, day and month budgets, the ticket budget, the project's day budget and the
    organisation's month budget. What is left under a scope is its cap, minus the ledger, minus the
    keys still open at that scope (S27-11). A trial has no day or month budget: its runs belong to
    no agent, so no spend is counted against one (S32-03).

    Serialized per scope: the scopes this stage reserves from are locked until the transaction ends
    (`lock_budget_scopes`), so a second stage reads the first one's reservation instead of the same
    remainder."""
    from choregos_api.db.models import Organization
    from choregos_api.services.agents import spend_this_month, spend_today
    from choregos_api.services.budgets import (
        lock_budget_scopes,
        open_reservations,
        org_spent_since,
        project_spent_since,
        reserve,
        start_of_day,
        start_of_month,
        ticket_budget,
        ticket_spent,
    )
    from choregos_api.services.definitions import ceiling_of

    engine = bundle.engine
    org_id = bundle.project.org_id
    org_cap = None
    if (ceiling := ceiling_of(await session.get(Organization, org_id))) is not None:
        org_cap = ceiling.budgets.monthly_org_usd
    agent_slug = agent.agent.slug if agent is not None and not trial else None
    agent_capped = (
        agent is not None
        and not trial
        and (agent.spec.budget.daily_usd is not None or agent.spec.budget.monthly_usd is not None)
    )
    scopes = [f"ticket:{item.id}"]
    if engine.daily_budget() is not None:
        scopes.append(f"project:{bundle.project.id}")
    if org_cap is not None:
        scopes.append(f"org:{org_id}")
    if agent_capped:
        scopes.append(f"agent:{org_id}:{agent_slug}")
    await lock_budget_scopes(session, scopes)

    left: dict[str, float | None] = {"stage": stage_usd}
    if agent is not None:
        left["agent_run"] = agent.spec.budget.run_usd
        if agent.spec.budget.daily_usd is not None and not trial:
            spent = await spend_today(session, org_id, agent.agent.slug)
            held = await open_reservations(
                session, org_id=org_id, agent_slug=agent.agent.slug, exclude_run=run_id
            )
            left["agent_day"] = agent.spec.budget.daily_usd - spent - held
        if agent.spec.budget.monthly_usd is not None and not trial:
            # The monthly cap is a reservation too (S27-09): a run cannot spend past it, nor can
            # the agent's runs in flight, each of which may still spend its key's cap (S27-11).
            month = await spend_this_month(session, org_id, agent.agent.slug)
            held = await open_reservations(
                session, org_id=org_id, agent_slug=agent.agent.slug, exclude_run=run_id
            )
            left["agent_month"] = agent.spec.budget.monthly_usd - month - held
    left["ticket"] = (
        ticket_budget(engine, item)
        - await ticket_spent(session, item.id, item.recurred_at)
        - await open_reservations(session, work_item_id=item.id, exclude_run=run_id)
    )
    if (day := engine.daily_budget()) is not None:
        left["project_day"] = (
            day
            - await project_spent_since(session, bundle.project.id, start_of_day())
            - await open_reservations(session, project_id=bundle.project.id, exclude_run=run_id)
        )
    if org_cap is not None:
        left["org_month"] = (
            org_cap
            - await org_spent_since(session, org_id, start_of_month())
            - await open_reservations(session, org_id=org_id, exclude_run=run_id)
        )
    return reserve(left)


async def _stage_cap(
    session: Any, bundle: Any, item: Any, run_id: str, plan: StagePlan
) -> dict[str, Any] | None:
    """The work item ran every agent stage the policy allows (`attempts.max_stages_per_ticket`,
    S27-08): no key, no run, a person decides. Counted from the runs table, so a restart, a replay or
    a resume does not reset it."""
    from sqlalchemy import func

    cap = bundle.engine.max_stages_per_ticket()
    ran = (
        await session.execute(
            select(func.count()).select_from(Run).where(Run.work_item_id == item.id, Run.id != run_id)
        )
    ).scalar_one()
    if ran < cap:
        return None
    logger.info("stage refused: stage cap", run_id=run_id, ran=ran, cap=cap)
    detail = (
        f"the stage `{plan.role}` did not start: the work item already ran {ran} agent stages, "
        f"the policy's max_stages_per_ticket is {cap}"
    )
    return {"run_id": run_id, "refused": {"reason": "max_stages", "detail": detail}}


async def _refuse_on_budget(
    session: Any, bundle: Any, item: Any, run_id: str, plan: StagePlan, reservation: Any
) -> dict[str, Any]:
    """Not enough left to start: no key, no run. The interpreter sends the ticket to a person."""
    detail = f"the stage `{plan.role}` did not start: {reservation.describe()}"
    logger.info("stage refused on budget", run_id=run_id, bound_by=reservation.bound_by)
    await persist_event(
        session,
        EventType.COST_ALERT,
        project_id=bundle.project.id,
        work_item_id=item.id,
        project_slug=bundle.slug,
        subject=item.tracker_key,
        reason="budget",
        stage=plan.role,
        bound_by=reservation.bound_by,
        left=reservation.left,
    )
    return {"run_id": run_id, "refused": {"reason": "budget", "detail": detail}}


async def _daily_budget(session: Any, bundle: Any, agent: Any) -> None:
    """An agent that has spent its daily budget (models and tools) launches no run before
    midnight (UTC). The refusal is not retried: retrying would not give the budget back."""
    from choregos_api.services.agents import spend_this_month, spend_today
    from choregos_api.services.budget_alerts import suspend_for_budget
    from temporalio.exceptions import ApplicationError

    slug = agent.agent.slug
    org_id = bundle.project.org_id
    cap = agent.spec.budget.daily_usd
    if cap is not None:
        spent_today = await spend_today(session, org_id, slug)
        if spent_today >= cap:
            raise ApplicationError(
                f"run refused before it started — the agent `{slug}` spent {spent_today:.2f} $ "
                f"today, for a daily budget of {cap:.2f} $",
                type="admission_refused",
                non_retryable=True,
            )
    monthly = agent.spec.budget.monthly_usd
    if monthly is not None:
        spent = await spend_this_month(session, org_id, slug)
        if spent >= monthly:
            # The hard stop of the monthly cap (S27-09); normally the spend that crossed it has
            # already suspended the agent, this covers a cap set below what was already spent.
            await suspend_for_budget(session, org_id, slug, spent=spent, cap=monthly)
            raise ApplicationError(
                f"run refused before it started — the agent `{slug}` spent {spent:.2f} $ "
                f"this month, for a monthly budget of {monthly:.2f} $",
                type="admission_refused",
                non_retryable=True,
            )
