# SPDX-License-Identifier: Apache-2.0
"""Who may do what with an assistant, and what a turn may spend (S63-15, ADR 0133 point 5, ADR 0134 point 3).

Three permissions, decided here and nowhere else:

- `assistant:manage`: the organisation administrator (the role carries it), or a member of one of the
  installation's owner groups.
- `assistant:use`: a member of one of the installation's audience groups. No organisation role carries it:
  an administrator who is not in the audience does not talk to the assistant.
- `assistant:audit`: the owners, the administrator, and the privacy officers. `privacy_officer` is not an
  organisation role (the `Role` enum is a published contract): it is a list of IdP groups the platform
  names in `CHOREGOS_ASSISTANT_PRIVACY_OFFICER_GROUPS`.

Groups come from the principal (`Principal.groups`, read from the user's row at each request), so leaving a
group takes effect at the next request, and the audience is evaluated again at every turn.

The three caps (`per_conversation_usd`, `per_user_month_usd`, `per_installation_month_usd`) come from the
manifest's `policy.budget`, overridden key by key by the installation's `policy_overrides.budget`. They are
turned into what is left under each, for the turn's key reservation.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import AssistantInstallation, CostLedger
from ..rbac import Permission, Principal
from .budgets import start_of_month

CAP_KEYS = ("per_conversation_usd", "per_user_month_usd", "per_installation_month_usd")


def holds(principal: Principal, permission: Permission, org: str, row: AssistantInstallation) -> bool:
    groups = set(principal.groups)
    if permission is Permission.ASSISTANT_USE:
        return org in principal.org_roles and bool(groups & set(row.audience_groups))
    if principal.can(permission, org) or groups & set(row.owner_groups):
        return True
    if permission is Permission.ASSISTANT_AUDIT:
        return bool(groups & set(get_settings().assistant_privacy_officer_groups))
    return False


def may_manage_groups(principal: Principal, org: str, owner_groups: list[str]) -> bool:
    """`assistant:manage` where only the owner groups are known (a listing)."""
    return principal.can(Permission.ASSISTANT_MANAGE, org) or bool(set(principal.groups) & set(owner_groups))


def caps_of(document: dict[str, Any] | None, row: AssistantInstallation) -> dict[str, float]:
    """The caps in force: the manifest's, then the installation's overrides on top. A value that is not a
    positive number is ignored."""
    declared = ((document or {}).get("policy") or {}).get("budget") or {}
    overrides = (row.policy_overrides or {}).get("budget") or {}
    caps: dict[str, float] = {}
    for key in CAP_KEYS:
        for source in (declared, overrides):
            value = source.get(key) if isinstance(source, dict) else None
            if isinstance(value, int | float) and not isinstance(value, bool) and value >= 0:
                caps[key] = float(value)
    return caps


async def month_spend(session: AsyncSession, installation_id: str, user_id: str | None = None) -> float:
    """What the conversations of the installation cost since the first of the month, from the ledger lines
    that name it (they outlive a deleted conversation), the person's alone when `user_id` is given."""
    query = select(func.coalesce(func.sum(CostLedger.cost_usd), 0.0)).where(
        CostLedger.installation_id == installation_id, CostLedger.ts >= start_of_month()
    )
    if user_id is not None:
        query = query.where(CostLedger.user_id == user_id)
    return float((await session.execute(query)).scalar_one())


async def turn_caps(
    session: AsyncSession, row: AssistantInstallation, user_id: str, conversation_spent: float
) -> dict[str, float]:
    """What is left under each of the three caps, named as `reserve_budget` reports them."""
    from .assistants import manifest

    caps = caps_of(await manifest(session, row), row)
    left: dict[str, float] = {}
    if "per_conversation_usd" in caps:
        left["assistant_conversation"] = caps["per_conversation_usd"] - conversation_spent
    if "per_user_month_usd" in caps:
        left["assistant_user_month"] = caps["per_user_month_usd"] - await month_spend(
            session, row.id, user_id
        )
    if "per_installation_month_usd" in caps:
        left["assistant_installation_month"] = caps["per_installation_month_usd"] - await month_spend(
            session, row.id
        )
    return left
