# SPDX-License-Identifier: Apache-2.0
"""How an agent and its versions are shown, shared by the agent routers (ADR 0099)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Agent, AgentVersion, HostedBuildRecord, Project, ProjectAgent, User
from ..rbac import Principal
from ..schemas import AgentDto, AgentSpec, AgentUpstream, AgentVersionDto
from .agent_governance import Action, decide, is_admin, permissions_of, subject_of
from .agent_lifecycle import last_run_at, unused
from .agent_releases import pending_release, policy_of, release_dto
from .hosted_builds import builds_for, with_current_build


async def agent_versions(session: AsyncSession, agent: Agent) -> list[AgentVersion]:
    rows = await session.execute(
        select(AgentVersion).where(AgentVersion.agent_id == agent.id).order_by(AgentVersion.version.desc())
    )
    return list(rows.scalars())


def agent_version_dto(
    row: AgentVersion,
    agent: Agent | None = None,
    pins: dict[int, list[str]] | None = None,
    builds: Mapping[str, HostedBuildRecord] | None = None,
) -> AgentVersionDto:
    channels: list[Literal["stable", "candidate"]] = []
    if agent is not None:
        if agent.stable_version == row.version:
            channels.append("stable")
        if agent.candidate_version == row.version:
            channels.append("candidate")
    return AgentVersionDto(
        version=row.version,
        spec=with_current_build(AgentSpec.model_validate(row.spec), builds or {}),
        checksum=row.checksum,
        created_by=row.created_by,
        created_at=row.created_at,
        message=row.message,
        restored_from=row.restored_from,
        pinned_by=sorted((pins or {}).get(row.version, [])),
        channels=channels,
    )


async def agent_pins(
    session: AsyncSession, principal: Principal, org: str, agent: Agent
) -> dict[int, list[str]]:
    """Version -> the slugs of the projects pinning it that the caller may see: an organisation admin
    sees them all, anyone else the projects they belong to (S40-12)."""
    rows = await session.execute(
        select(ProjectAgent.version, Project.slug)
        .join(Project, Project.id == ProjectAgent.project_id)
        .where(ProjectAgent.agent_id == agent.id)
    )
    pins: dict[int, list[str]] = {}
    for version, slug in rows:
        if version is None:  # a pin on a channel (ADR 0071) names no revision: the channel badges tell
            continue
        if is_admin(principal, org) or principal.role_for(org, slug) is not None:
            pins.setdefault(version, []).append(slug)
    return pins


async def agent_upstream(
    session: AsyncSession, agent: Agent, principal: Principal, org: str
) -> AgentUpstream | None:
    """What the fork was copied from; the source's state only when the caller may see it (ADR 0061)."""
    if agent.forked_from_slug is None or agent.forked_from_version is None:
        return None
    source = await session.get(Agent, agent.forked_from_agent_id) if agent.forked_from_agent_id else None
    if source is None or not decide(principal, org, await subject_of(session, source), Action.SEE).allowed:
        return AgentUpstream(slug=agent.forked_from_slug, version=agent.forked_from_version, available=False)
    latest = (
        await session.execute(
            select(func.max(AgentVersion.version)).where(AgentVersion.agent_id == source.id)
        )
    ).scalar() or 0
    return AgentUpstream(
        slug=source.slug,
        version=agent.forked_from_version,
        scope=source.scope,
        latest_version=latest,
        behind=latest > agent.forked_from_version,
    )


async def agent_dto(
    session: AsyncSession, agent: Agent, principal: Principal, org: str, *, with_versions: bool = False
) -> AgentDto:
    versions = await agent_versions(session, agent)
    owner = await session.get(User, agent.owner_id) if agent.owner_id else None
    last_run = await last_run_at(session, agent.org_id, agent.slug)
    subject = await subject_of(session, agent)
    pins = await agent_pins(session, principal, org, agent) if with_versions else None
    builds = await builds_for(session, agent.org_id, [v.spec for v in versions]) if with_versions else {}
    rights, reason_why = permissions_of(principal, org, subject)
    return AgentDto(
        scope=agent.scope,
        project=subject.project_slug,
        forked_from=await agent_upstream(session, agent, principal, org),
        can=rights,
        cannot_reason=reason_why,
        slug=agent.slug,
        kind=agent.kind,
        display_name=agent.display_name,
        description=agent.description,
        tags=list(agent.tags or []),
        status=agent.status,
        owner=owner.email if owner else None,
        expires_at=agent.expires_at,
        revoked_at=agent.revoked_at,
        latest_version=versions[0].version if versions else 0,
        created_at=agent.created_at,
        versions=[agent_version_dto(v, agent, pins, builds) for v in versions] if pins is not None else None,
        lifecycle=agent.lifecycle or "active",
        retired_at=agent.retired_at,
        last_run_at=last_run,
        unused=unused(agent, last_run),
        stable_version=agent.stable_version,
        candidate_version=agent.candidate_version,
        candidate_percent=agent.candidate_percent or 0,
        candidate_since=agent.candidate_since,
        release_policy=policy_of(agent),
        pending_release=release_dto(waiting, agent.slug)
        if (waiting := await pending_release(session, agent))
        else None,
    )
