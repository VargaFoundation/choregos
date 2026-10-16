# SPDX-License-Identifier: Apache-2.0
"""Budget alerts, as the inbox reads them (S29-04): a project's or an agent's daily budget crossed
80 % or 100 %. Raised by the spend itself (`services.budget_alerts`); a person acknowledges them."""

from __future__ import annotations

from typing import Annotated

from choregos_core import utcnow
from fastapi import APIRouter, Query
from sqlalchemy import select

from ..audit import record
from ..db.models import BudgetAlert, Organization
from ..deps import Db, Me
from ..errors import conflict, forbidden, not_found
from ..rbac import Permission
from ..schemas import BudgetAlertDto

router = APIRouter(tags=["costs"])


async def _organisation(session: Db, slug: str) -> Organization:
    organisation = (
        await session.execute(select(Organization).where(Organization.slug == slug))
    ).scalar_one_or_none()
    if organisation is None:
        raise not_found("Organisation", slug)
    return organisation


def _dto(alert: BudgetAlert) -> BudgetAlertDto:
    return BudgetAlertDto.model_validate(alert)


@router.get("/orgs/{org}/budget-alerts", response_model=list[BudgetAlertDto], operation_id="listBudgetAlerts")
async def list_budget_alerts(
    org: str,
    session: Db,
    principal: Me,
    status: Annotated[str, Query(pattern="^(open|acknowledged|all)$")] = "open",
) -> list[BudgetAlertDto]:
    """The budgets that crossed a threshold, newest first; `open` (the default) are the inbox's."""
    if not principal.can(Permission.PROJECT_READ, org):
        raise forbidden(f"reading the budget alerts of {org} requires being a member of it")
    organisation = await _organisation(session, org)
    query = select(BudgetAlert).where(BudgetAlert.org_id == organisation.id)
    if status != "all":
        query = query.where(BudgetAlert.status == status)
    rows = (await session.execute(query.order_by(BudgetAlert.created_at.desc(), BudgetAlert.id))).scalars()
    return [_dto(row) for row in rows]


@router.post(
    "/orgs/{org}/budget-alerts/{alert_id}/acknowledge",
    response_model=BudgetAlertDto,
    operation_id="acknowledgeBudgetAlert",
)
async def acknowledge_budget_alert(org: str, alert_id: str, session: Db, principal: Me) -> BudgetAlertDto:
    """Someone has seen it: it leaves the inbox. The next threshold, or the next day, raises a new one.

    Only a project owner or an organisation administrator may (decision of 2026-10-16): acknowledging
    takes the alert out of everyone's inbox, so it belongs to whoever can act on the budget. A
    project's alert is checked against that project; an agent's, which spans the organisation,
    against the organisation role.
    """
    if not principal.can(Permission.PROJECT_READ, org):
        raise forbidden(f"acknowledging a budget alert of {org} requires being a member of it")
    organisation = await _organisation(session, org)
    alert = await session.get(BudgetAlert, alert_id)
    if alert is None or alert.org_id != organisation.id:
        raise not_found("Budget alert", alert_id)
    project = alert.subject if alert.scope == "project" else None
    if not principal.can(Permission.PROJECT_WRITE, org, project):
        raise forbidden("acknowledging a budget alert needs the project owner role or above")
    if alert.status != "open":
        raise conflict("this budget alert is already acknowledged")
    alert.status = "acknowledged"
    alert.acknowledged_by = principal.email
    alert.acknowledged_at = utcnow()
    await session.flush()
    await record(
        session,
        principal,
        "budget_alert.acknowledge",
        org_id=organisation.id,
        target_type="budget_alert",
        target_id=alert.id,
        scope=alert.scope,
        subject=alert.subject,
        threshold=alert.threshold,
    )
    return _dto(alert)
