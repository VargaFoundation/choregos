# SPDX-License-Identifier: Apache-2.0
"""Who answers a waiting human request (ADR 0153): a person, a group, or a person first.

A workflow's human actor names a `group`, a `user` (an e-mail), or both. The rules, checked where a
person answers, on top of the rank (`ITEM_DECIDE`):

- **a person alone**: only that person answers;
- **a group alone**: a member of the group answers (ADR 0044, unchanged);
- **both**: the person is asked first; once the request's SLA has passed (its `due_at`), a member of
  the group may answer too, and the person still may;
- **an organisation administrator may always answer**, audited as `workitem.decision.group_override`
  with what it bypassed, so that a step addressed to someone who left never waits forever.

An organisation administrator may also **reassign** a waiting request: the new assignee is kept on
the request row (`human_requests.assignee`) and replaces the workflow's actor for that request only.
The workflow is not changed, and its digest does not move.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from choregos_contracts import HumanActor, Role, Workflow
from choregos_core import utcnow
from choregos_core.dsl.groups import group_for
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import HumanRequest, Membership, Project, User, WorkItem
from ..rbac import Principal
from .definitions import work_item_workflow, workflow_model
from .human_groups import groups_for_org
from .human_requests import DEFAULT_SLA_HOURS

Verdict = Literal["ok", "override", "refused"]


@dataclass(frozen=True)
class StepAssignee:
    """Who a request is addressed to. `user` is lowercase; `group` is resolved for the project."""

    user: str | None
    group: str | None
    #: True when an organisation administrator reassigned the request (the row's override).
    reassigned: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"user": self.user, "group": self.group, "reassigned": self.reassigned}


def assignee_from_actor(actor: HumanActor | None, project_slug: str | None) -> StepAssignee | None:
    """What a workflow's human actor names, or `None` when it names nobody (an empty group)."""
    if actor is None:
        return None
    group = group_for(actor.group, project_slug) if actor.group and actor.group.strip() else None
    user = actor.user.strip().lower() if actor.user else None
    if user is None and group is None:
        return None
    return StepAssignee(user=user, group=group)


def assignee_of_row(row: HumanRequest) -> StepAssignee | None:
    """The override an administrator recorded on the request, or `None`."""
    override = row.assignee or None
    if not override:
        return None
    user = override.get("user")
    group = override.get("group")
    return StepAssignee(
        user=str(user).lower() if user else None, group=str(group) if group else None, reassigned=True
    )


def actor_of_the_step(workflow: Workflow, transition_id: str | None) -> HumanActor | None:
    """The human actor of the transition a request belongs to, or `None`."""
    if not transition_id:
        return None
    for transition in workflow.transitions:
        if transition.key == transition_id:
            actor = workflow.actor_of(transition)
            return actor if isinstance(actor, HumanActor) else None
    return None


async def assignee_of_the_request(
    session: AsyncSession, item: WorkItem, row: HumanRequest
) -> StepAssignee | None:
    """Who the request is addressed to now: the administrator's override, else the workflow's actor
    (read from the workflow the work item was born in, at the request's transition)."""
    override = assignee_of_row(row)
    if override is not None:
        return override
    actor = actor_of_the_step(workflow_model(await work_item_workflow(session, item)), row.transition_id)
    if actor is None:
        return None
    slug = (
        await session.execute(select(Project.slug).where(Project.id == item.project_id))
    ).scalar_one_or_none()
    return assignee_from_actor(actor, slug)


def sla_passed(row: HumanRequest, now: datetime | None = None) -> bool:
    """Whether the request's SLA has passed: its `due_at`, else 24 h after it was asked."""
    due = row.due_at or (row.requested_at + timedelta(hours=DEFAULT_SLA_HOURS))
    if due.tzinfo is None:  # SQLite hands back a naive UTC datetime
        due = due.replace(tzinfo=UTC)
    return (now or utcnow()) >= due


def decider_verdict(
    principal: Principal, org_slug: str, assignee: StepAssignee | None, *, after_sla: bool
) -> Verdict:
    """`ok`, `override` (an organisation administrator the step is not addressed to) or `refused`."""
    if assignee is None:
        return "ok"
    if assignee.user and (principal.email or "").strip().lower() == assignee.user:
        return "ok"
    group_may_answer = assignee.group is not None and (assignee.user is None or after_sla)
    if group_may_answer and assignee.group in groups_for_org(principal.groups, org_slug):
        return "ok"
    if principal.org_roles.get(org_slug) == Role.ORG_ADMIN:
        return "override"
    return "refused"


def refusal(assignee: StepAssignee, *, after_sla: bool) -> str:
    """Why a person may not answer, in words that say who may."""
    groups = "(identity-provider groups, as of your last sign-in)"
    if assignee.user and assignee.group and not after_sla:
        return (
            f"this step is addressed to `{assignee.user}` first: the group `{assignee.group}` may "
            "answer it once its SLA has passed"
        )
    if assignee.user and assignee.group:
        return (
            f"this step is addressed to `{assignee.user}` and, its SLA passed, to the group "
            f"`{assignee.group}`: only they answer it {groups}"
        )
    if assignee.user:
        return f"this step is addressed to `{assignee.user}`: only that person answers it"
    return f"this step is addressed to the group `{assignee.group}`: only its members answer it {groups}"


async def users_known_in(session: AsyncSession, org_id: str) -> set[str]:
    """The e-mail addresses of the organisation's members, lowercase (any membership counts)."""
    rows = (
        await session.execute(
            select(func.lower(User.email))
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.org_id == org_id)
        )
    ).scalars()
    return {str(email) for email in rows if email}


def unknown_user_warnings(workflow: Any, known: set[str]) -> list[dict[str, Any]]:
    """A human actor whose `user` is not a member of the organisation: a step addressed to them waits
    until an organisation administrator answers or reassigns it."""
    issues = []
    for actor_id, actor in workflow.actors.items():
        if not isinstance(actor, HumanActor) or not actor.user:
            continue
        if actor.user.strip().lower() in known:
            continue
        issues.append(
            {
                "code": "actor.user_unknown",
                "message": (
                    f"the person `{actor.user}` of actor `{actor_id}` is not a member of this "
                    "organisation: a step addressed to them waits until an organisation administrator "
                    "answers or reassigns it"
                ),
                "path": f"/actors/{actor_id}/user",
            }
        )
    return issues
