# SPDX-License-Identifier: Apache-2.0
"""Budget alerts, as the inbox reads them (S29-04): a project's or an agent's daily budget crossed
80 % or 100 %. Raised by the spend itself (`services.budget_alerts`); a person acknowledges them."""

from __future__ import annotations

from typing import Annotated

from choregos_core import utcnow
from fastapi import APIRouter, Query
from sqlalchemy import select

from ..audit import record
from ..db.models import BudgetAlert
from ..deps import Db, Me
from ..errors import conflict, forbidden, not_found
from ..rbac import Permission, Principal
from ..schemas import BudgetAlertDto
from ..services.organisations import organisation_by_slug

router = APIRouter(tags=["costs"])


def _may_acknowledge(principal: Principal, org: str, alert: BudgetAlert) -> bool:
    """A project's alert against that project, an agent's (organisation-wide) against the org role."""
    project = alert.subject if alert.scope == "project" else None
    return principal.can(Permission.PROJECT_WRITE, org, project)


def _is_member(principal: Principal, org: str) -> bool:
    """A role on the organisation, or on one of its projects."""
    return org in principal.org_roles or any(key.startswith(f"{org}/") for key in principal.project_roles)


def _dto(alert: BudgetAlert, principal: Principal, org: str) -> BudgetAlertDto:
    dto = BudgetAlertDto.model_validate(alert)
    dto.can_acknowledge = alert.status == "open" and _may_acknowledge(principal, org, alert)
    return dto


@router.get("/orgs/{org}/budget-alerts", response_model=list[BudgetAlertDto], operation_id="listBudgetAlerts")
async def list_budget_alerts(
    org: str,
    session: Db,
    principal: Me,
    status: Annotated[str, Query(pattern="^(open|acknowledged|all)$")] = "open",
) -> list[BudgetAlertDto]:
    """The budgets that crossed a threshold, newest first; `open` (the default) are the inbox's.

    Like the actions of the inbox, the caller reads what they can see, not a refusal: a member of one
    project only, with no organisation role, reads that project's alerts. An agent's alert spans the
    organisation (an agent serves several projects), so it needs a role on the organisation.
    """
    if not _is_member(principal, org):
        return []
    organisation = await organisation_by_slug(session, org)
    query = select(BudgetAlert).where(BudgetAlert.org_id == organisation.id)
    if status != "all":
        query = query.where(BudgetAlert.status == status)
    rows = (await session.execute(query.order_by(BudgetAlert.created_at.desc(), BudgetAlert.id))).scalars()
    return [_dto(row, principal, org) for row in rows if _may_read(principal, org, row)]


def _may_read(principal: Principal, org: str, alert: BudgetAlert) -> bool:
    project = alert.subject if alert.scope == "project" else None
    return principal.can(Permission.PROJECT_READ, org, project)


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
    if not _is_member(principal, org):
        raise forbidden(f"acknowledging a budget alert of {org} requires being a member of it")
    organisation = await organisation_by_slug(session, org)
    alert = await session.get(BudgetAlert, alert_id)
    if alert is None or alert.org_id != organisation.id:
        raise not_found("Budget alert", alert_id)
    if not _may_acknowledge(principal, org, alert):
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
    return _dto(alert, principal, org)
