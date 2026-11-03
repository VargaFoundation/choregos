# SPDX-License-Identifier: Apache-2.0
"""The lifecycle of an agent (S32-04): draft → active → deprecated → retired.

Kept apart from the operational `status` (active, suspended, revoked — a budget kill switch, an
administrator's pause): the lifecycle says what the organisation intends for the agent, the status
whether it may run right now. Both must allow a run.

- `draft`: being written; no project pins it, no run starts with it.
- `active`: in service.
- `deprecated`: still runs, but a pin is answered with a warning and the console says so.
- `retired`: final. Retiring executes a checklist — its credentials are revoked, its project pins
  removed, new runs refused — and refuses while one of its runs is in flight, unless forced.

An agent that never served — no pin, no run outside its trials, named by no active workflow — can
also be deleted outright (`delete_blockers`); one that served is retired, so its history keeps a name.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from choregos_core import utcnow
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    Agent,
    AgentCredential,
    AgentRelease,
    Project,
    ProjectAgent,
    Run,
    WorkflowDef,
    WorkItem,
)
from ..deps import _aware
from .definitions import workflow_model
from .workflow_catalogue import named_agents

LIFECYCLES = ("draft", "active", "deprecated", "retired")
#: The transitions a person may ask for; `retired` is final.
TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"active", "retired"}),
    "active": frozenset({"deprecated", "retired"}),
    "deprecated": frozenset({"active", "retired"}),
    "retired": frozenset(),
}
IN_FLIGHT = ("queued", "running")
UNUSED_DAYS = 60


class TransitionRefused(ValueError):  # noqa: N818 - a refusal with its reason, rendered as 409
    pass


def check_transition(current: str, target: str) -> None:
    if target not in TRANSITIONS.get(current, frozenset()):
        if current == "retired":
            raise TransitionRefused("the agent is retired: retirement is final")
        raise TransitionRefused(f"an agent cannot go from `{current}` to `{target}`")


async def running_work_items(session: AsyncSession, org_id: str, slug: str) -> list[str]:
    """The work items with a run of this agent still in flight, by tracker key."""
    lignes = await session.execute(
        select(WorkItem.tracker_key)
        .join(Run, Run.work_item_id == WorkItem.id)
        .join(Project, Project.id == Run.project_id)
        .where(Project.org_id == org_id, Run.agent_slug == slug, Run.status.in_(IN_FLIGHT))
        .distinct()
        .order_by(WorkItem.tracker_key)
    )
    return [str(cle) for (cle,) in lignes.tuples()]


async def last_run_at(session: AsyncSession, org_id: str, slug: str) -> datetime | None:
    return (
        await session.execute(
            select(func.max(Run.created_at))
            .join(Project, Project.id == Run.project_id)
            .where(Project.org_id == org_id, Run.agent_slug == slug)
        )
    ).scalar()


def unused(agent: Agent, dernier: datetime | None, now: datetime | None = None) -> bool:
    """No run for `UNUSED_DAYS` — counted from its creation when it never ran. A retired agent is
    not "unused": it is gone."""
    if agent.lifecycle == "retired":
        return False
    seuil = (now or utcnow()) - timedelta(days=UNUSED_DAYS)
    reference = dernier or agent.created_at
    return reference is not None and _aware(reference) < seuil


async def retire(session: AsyncSession, agent: Agent) -> dict[str, Any]:
    """The checklist, executed: credentials revoked, project pins removed, the agent retired. The
    caller has already refused (or forced past) the runs in flight; it records the result."""
    maintenant = utcnow()
    accreditations = list(
        (
            await session.execute(
                select(AgentCredential).where(
                    AgentCredential.agent_id == agent.id, AgentCredential.revoked_at.is_(None)
                )
            )
        ).scalars()
    )
    for accreditation in accreditations:
        accreditation.revoked_at = maintenant
    epingles = (
        await session.execute(
            select(ProjectAgent, Project.slug)
            .join(Project, Project.id == ProjectAgent.project_id)
            .where(ProjectAgent.agent_id == agent.id)
            .order_by(Project.slug)
        )
    ).tuples()
    projets: list[str] = []
    for epingle, projet in epingles.all():
        projets.append(str(projet))
        await session.delete(epingle)
    agent.lifecycle = "retired"
    agent.retired_at = maintenant
    # Nothing stays in testing for an agent that will never run again (ADR 0071).
    agent.candidate_version, agent.candidate_percent, agent.candidate_since = None, 0, None
    # ... nor a release that waits for approval to put a revision in production.
    for waiting in (
        await session.execute(
            select(AgentRelease).where(
                AgentRelease.agent_id == agent.id, AgentRelease.status == "pending_approval"
            )
        )
    ).scalars():
        waiting.status = "cancelled"
    await session.flush()
    return {"revoked_credentials": len(accreditations), "unpinned_projects": projets}


#: The state of the work item an agent trial runs on (`routage.nouveau_ticket_d_essai`): a trial is
#: how an agent is tried before it serves, so it does not count as having served.
TRIAL_STATE = "trial"


async def delete_blockers(session: AsyncSession, org_id: str, agent: Agent) -> list[str]:
    """Why the agent cannot be deleted, in plain words; empty when it never served.

    Deleting is offered only to an agent nothing depends on and nothing remembers: a project pin, a
    run outside its trials, or an active workflow that names it all mean it served or will. Those
    are retired instead, which keeps their name in the history."""
    reasons: list[str] = []
    pinned = (
        await session.execute(
            select(Project.slug)
            .join(ProjectAgent, ProjectAgent.project_id == Project.id)
            .where(ProjectAgent.agent_id == agent.id)
            .order_by(Project.slug)
        )
    ).scalars()
    if projects := list(pinned):
        reasons.append(f"it is pinned by {', '.join(projects)}")
    runs = (
        await session.execute(
            select(func.count())
            .select_from(Run)
            .join(Project, Project.id == Run.project_id)
            .join(WorkItem, WorkItem.id == Run.work_item_id)
            .where(Project.org_id == org_id, Run.agent_slug == agent.slug, WorkItem.state != TRIAL_STATE)
        )
    ).scalar_one()
    if runs:
        reasons.append(f"it has run {runs} time{'s' if runs != 1 else ''}")
    naming = (
        await session.execute(
            select(Project.slug, WorkflowDef)
            .join(Project, Project.id == WorkflowDef.project_id)
            .where(Project.org_id == org_id, WorkflowDef.is_active.is_(True))
            .order_by(Project.slug, WorkflowDef.name)
        )
    ).tuples()
    workflows = [
        f"{project}/{row.name}"
        for project, row in naming.all()
        if any(slug == agent.slug for _, slug, _ in named_agents(workflow_model(row)))
    ]
    if workflows:
        several = len(workflows) != 1
        reasons.append(
            f"the workflow{'s' if several else ''} {', '.join(workflows)} name{'' if several else 's'} it"
        )
    return reasons


__all__ = [
    "IN_FLIGHT",
    "LIFECYCLES",
    "TRANSITIONS",
    "TRIAL_STATE",
    "UNUSED_DAYS",
    "TransitionRefused",
    "check_transition",
    "delete_blockers",
    "last_run_at",
    "retire",
    "running_work_items",
    "unused",
]
