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

from collections.abc import Iterable
from typing import Any

from choregos_contracts import HumanActor, Role
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Membership, Organization, User, WorkItem
from ..rbac import Principal
from .definitions import workflow_du_ticket, workflow_model


def groups_for_org(groups: Iterable[Any] | None, org_slug: str) -> set[str]:
    """The groups a person carries **for one organisation** (S40-27).

    The identity provider may send a group bare (`architects`) or namespaced for an organisation
    (`choregos:diametral:architects`, the same prefix that carries the role). For `org_slug` both count,
    as the bare name; a group namespaced for **another** organisation does not, so two organisations of
    one instance never confuse their `architects`. `User.groups` stays as sent.
    """
    prefix = f"choregos:{org_slug}:"
    carried: set[str] = set()
    for raw in groups or []:
        group = str(raw)
        carried.add(group)
        if group.startswith(prefix) and len(group) > len(prefix):
            carried.add(group[len(prefix) :])
    return carried


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
    if group is None or group in groups_for_org(principal.groups, org_slug):
        return "ok"
    if principal.org_roles.get(org_slug) == Role.ORG_ADMIN:
        return "override"
    return "refused"


async def groups_known_in(session: AsyncSession, org_id: str) -> set[str]:
    """The groups the identity provider has sent for the organisation's members at their sign-ins, as
    the organisation names them (a `choregos:<slug>:<group>` of this organisation counts as `<group>`;
    the slug is read now, it can change)."""
    org_slug = (
        await session.execute(select(Organization.slug).where(Organization.id == org_id))
    ).scalar_one_or_none()
    rows = (
        await session.execute(
            select(User.groups)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.org_id == org_id)
        )
    ).scalars()
    known: set[str] = set()
    for groups in rows:
        known |= groups_for_org(groups, org_slug) if org_slug else {str(g) for g in groups or []}
    return known


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


async def members_of_group(
    session: AsyncSession, org_id: str, org_slug: str, project_id: str, group: str
) -> list[tuple[User, bool]]:
    """The organisation's members who carry `group` (as of their last sign-in), each with whether they
    may answer a step of this project: the group is necessary, not sufficient — `item:decide` comes
    from a role in the organisation or on the project (ADR 0044)."""
    from ..rbac import ROLE_PERMISSIONS, Permission

    rows = (
        await session.execute(
            select(User, Membership)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.org_id == org_id)
            .order_by(User.email)
        )
    ).all()
    found: dict[str, tuple[User, bool]] = {}
    for user, membership in rows:
        if group not in groups_for_org(user.groups, org_slug):
            continue
        reaches = membership.project_id in (None, project_id)
        decides = reaches and Permission.ITEM_DECIDE in ROLE_PERMISSIONS.get(
            Role(membership.role), frozenset()
        )
        _, before = found.get(user.id, (user, False))
        found[user.id] = (user, before or decides)
    return list(found.values())
