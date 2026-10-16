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
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from choregos_core import utcnow
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Agent, AgentCredential, Project, ProjectAgent, Run, WorkItem
from ..deps import _aware

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
    await session.flush()
    return {"revoked_credentials": len(accreditations), "unpinned_projects": projets}


__all__ = [
    "IN_FLIGHT",
    "LIFECYCLES",
    "TRANSITIONS",
    "UNUSED_DAYS",
    "TransitionRefused",
    "check_transition",
    "last_run_at",
    "retire",
    "running_work_items",
    "unused",
]
