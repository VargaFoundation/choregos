# SPDX-License-Identifier: Apache-2.0
"""Who may read, change or see an agent, shared by the agent routers (ADR 0099)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Agent, Organization, Project
from ..errors import forbidden, not_found, unprocessable
from ..rbac import Principal
from .agent_governance import Action, Decision, Subject, decide, is_member, subject_of
from .organisations import organisation_by_slug


def require_agent_reader(principal: Principal, org: str) -> None:
    """Reading the organisation's agents requires being a member of it (403 otherwise)."""
    if not is_member(principal, org):
        raise forbidden(f"reading the agents of {org} requires being a member of it")


def enforce_decision(decision: Decision) -> None:
    """A refusal says why: 403 for the caller's rights, 422 for a request that cannot be."""
    if decision.allowed:
        return
    reason = decision.reason or "insufficient rights"
    raise unprocessable(reason) if decision.unprocessable else forbidden(reason)


async def agent_in_org(session: AsyncSession, organisation: Organization, slug: str) -> Agent:
    """The organisation's agent with this slug, or a 404."""
    agent = (
        await session.execute(select(Agent).where(Agent.org_id == organisation.id, Agent.slug == slug))
    ).scalar_one_or_none()
    if agent is None:
        raise not_found("Agent", f"{organisation.slug}/{slug}")
    return agent


async def load_visible_agent(
    session: AsyncSession, principal: Principal, org: str, slug: str
) -> tuple[Organization, Agent, Subject]:
    """The agent, for a caller who may see it: a personal agent of someone else, or a project's agent
    the caller does not belong to, is a 404 — never a 403 that would reveal it exists."""
    require_agent_reader(principal, org)
    organisation = await organisation_by_slug(session, org)
    agent = await agent_in_org(session, organisation, slug)
    subject = await subject_of(session, agent)
    if not decide(principal, org, subject, Action.SEE).allowed:
        raise not_found("Agent", f"{org}/{slug}")
    return organisation, agent, subject


async def project_in_org(session: AsyncSession, organisation: Organization, slug: str) -> Project:
    """The organisation's project with this slug, or a 404."""
    project = (
        await session.execute(select(Project).where(Project.org_id == organisation.id, Project.slug == slug))
    ).scalar_one_or_none()
    if project is None:
        raise not_found("Project", f"{organisation.slug}/{slug}")
    return project
