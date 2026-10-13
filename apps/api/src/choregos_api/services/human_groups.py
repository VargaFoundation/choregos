# SPDX-License-Identifier: Apache-2.0
"""A human actor's `group` is enforced where a person answers (ADR 0044, ADR 0052).

A workflow names who may answer a step: `actors.<id>: {type: human, group: release-captains}`. The
group is the identity provider's, as the principal carries it. Two rules:

- **the decider belongs to the group**, on top of the rank (`ITEM_DECIDE`): a project owner outside
  `release-captains` cannot answer a step addressed to it;
- **an organisation administrator may override**, and the override is never silent: it is audited as
  `workitem.decision.group_override` with the group it bypassed. Without it, a group the IdP never
  sends would leave a ticket that waits forever; the administrator is the way out.

The check reads the workflow the work item was born in, at the transition the request belongs to, so
nothing travels in the request row and no workflow code changes.
"""

from __future__ import annotations

from typing import Any

from choregos_contracts import HumanActor, Role
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Membership, User, WorkItem
from ..rbac import Principal
from .definitions import workflow_du_ticket, workflow_model


async def group_of_the_step(session: AsyncSession, item: WorkItem, transition_id: str | None) -> str | None:
    """The group the transition's human actor names — `None` when the step is not addressed to one."""
    if not transition_id:
        return None
    workflow = workflow_model(await workflow_du_ticket(session, item))
    for transition in workflow.transitions:
        if transition.key == transition_id:
            actor = workflow.actor_of(transition)
            if isinstance(actor, HumanActor) and actor.group.strip():
                return actor.group.strip()
    return None


def decider_group_verdict(principal: Principal, org_slug: str, group: str | None) -> str:
    """`ok`, `override` (an organisation administrator outside the group) or `refused`."""
    if group is None or group in {str(g) for g in principal.groups}:
        return "ok"
    if principal.org_roles.get(org_slug) == Role.ORG_ADMIN:
        return "override"
    return "refused"


async def groups_known_in(session: AsyncSession, org_id: str) -> set[str]:
    """The groups the identity provider has sent for the organisation's members at their sign-ins."""
    rows = (
        await session.execute(
            select(User.groups)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.org_id == org_id)
        )
    ).scalars()
    return {str(g) for groups in rows for g in (groups or [])}


def unknown_group_warnings(workflow: Any, known: set[str]) -> list[dict[str, Any]]:
    """A human actor whose group nobody in the organisation has ever carried: the ticket would wait
    for someone who cannot exist (product review, point 5)."""
    issues = []
    for actor_id, actor in workflow.actors.items():
        if isinstance(actor, HumanActor) and actor.group.strip() and actor.group.strip() not in known:
            issues.append(
                {
                    "code": "actor.group_unknown",
                    "message": (
                        f"the group `{actor.group}` of actor `{actor_id}` is carried by nobody in this "
                        "organisation (identity-provider groups, as of their last sign-in): a step "
                        "addressed to it waits until an organisation administrator answers"
                    ),
                    "path": f"/actors/{actor_id}/group",
                }
            )
    return issues
