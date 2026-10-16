# SPDX-License-Identifier: Apache-2.0
"""Release channels of an agent (S40-02, ADR 0071): send a revision to testing, to production, roll
back, read the history, the readiness of a revision and who is served what."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query, status

from ..audit import record
from ..db.models import Organization
from ..deps import Db, Me, ProjectCtx
from ..errors import forbidden
from ..fraicheur import exiger_une_authentification_fraiche
from ..rbac import Permission
from ..schemas import (
    AgentDto,
    AgentExposure,
    AgentReleaseCreate,
    AgentReleaseDto,
    AgentReleasePolicy,
    AgentReleaseReadiness,
    ProjectAgentChannel,
)
from ..services import agent_releases
from ..services.agent_governance import PERSONAL, Action, decide
from .agents import Slug, _charger, _dto, _exiger

router = APIRouter(tags=["agents"])


@router.get(
    "/orgs/{org}/agents/{slug}/releases",
    response_model=list[AgentReleaseDto],
    operation_id="listAgentReleases",
)
async def list_agent_releases(
    org: str,
    slug: Slug,
    session: Db,
    principal: Me,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[AgentReleaseDto]:
    """Every move of the agent's channels, newest first: the release history."""
    _, agent, _ = await _charger(session, principal, org, slug)
    rows = await agent_releases.history(session, agent, limit)
    return [agent_releases.release_dto(row, agent.slug) for row in rows]


@router.post(
    "/orgs/{org}/agents/{slug}/releases",
    response_model=AgentReleaseDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="releaseAgentVersion",
)
async def release_agent_version(
    org: str, slug: Slug, body: AgentReleaseCreate, session: Db, principal: Me
) -> AgentReleaseDto:
    """Point a channel at a revision: testing (with a canary share), or production — which checks the
    eval suite again, the candidate's record, needs a person with a fresh sign-in, and the plugin's
    `agent.release` gesture check. Rolling back to a revision that was in production skips the gates."""
    organisation, agent, subject = await _charger(session, principal, org, slug)
    _exiger(decide(principal, org, subject, Action.RELEASE))
    row = await agent_releases.release(session, principal, organisation, agent, body)
    return agent_releases.release_dto(row, agent.slug)


@router.get(
    "/orgs/{org}/agents/{slug}/versions/{version}/readiness",
    response_model=AgentReleaseReadiness,
    operation_id="getAgentReleaseReadiness",
)
async def get_agent_release_readiness(
    org: str,
    slug: Slug,
    version: int,
    session: Db,
    principal: Me,
    channel: Annotated[Literal["stable", "candidate"], Query()] = "stable",
) -> AgentReleaseReadiness:
    """What releasing this revision to a channel would check, without doing it."""
    _, agent, _ = await _charger(session, principal, org, slug)
    return await agent_releases.readiness(session, principal, agent, version, channel)


@router.put(
    "/orgs/{org}/agents/{slug}/release-policy",
    response_model=AgentDto,
    operation_id="setAgentReleasePolicy",
)
async def set_agent_release_policy(
    org: str, slug: Slug, body: AgentReleasePolicy, session: Db, principal: Me
) -> AgentDto:
    """What saving a revision does, and the gates of production. Loosening them (publish straight to
    production, a lower threshold, a longer sign-in age) needs a person with a fresh sign-in."""
    organisation, agent, subject = await _charger(session, principal, org, slug)
    _exiger(decide(principal, org, subject, Action.RELEASE))
    before = agent_releases.policy_of(agent)
    if agent.scope != PERSONAL and agent_releases.loosens(before, body):
        if not agent_releases.is_human_session(principal):
            raise forbidden("loosening a release policy is done in a human session, not with an API token")
        exiger_une_authentification_fraiche(principal, before.stable.step_up_minutes)
    agent.release_policy = body.model_dump(mode="json")
    await session.flush()
    await record(
        session,
        principal,
        "agent.release_policy",
        org_id=organisation.id,
        target_type="agent",
        target_id=agent.id,
        before=before.model_dump(mode="json"),
        after=body.model_dump(mode="json"),
    )
    return await _dto(session, agent, principal, org, avec_versions=True)


@router.get(
    "/orgs/{org}/agents/{slug}/exposure", response_model=AgentExposure, operation_id="getAgentExposure"
)
async def get_agent_exposure(org: str, slug: Slug, session: Db, principal: Me) -> AgentExposure:
    """Production, testing, and the projects (among those the caller may read) that chose their own."""
    organisation, agent, _ = await _charger(session, principal, org, slug)
    return await agent_releases.exposure(session, principal, organisation, agent)


@router.get(
    "/projects/{id}/agent-channel",
    response_model=ProjectAgentChannel,
    operation_id="getProjectAgentChannel",
)
async def get_project_agent_channel(ctx: ProjectCtx) -> ProjectAgentChannel:
    """Which channel of the agents the project runs when it pins nothing."""
    return ProjectAgentChannel(channel=ctx.project.agent_channel)


@router.put(
    "/projects/{id}/agent-channel",
    response_model=ProjectAgentChannel,
    operation_id="setProjectAgentChannel",
)
async def set_project_agent_channel(
    body: ProjectAgentChannel, ctx: ProjectCtx, session: Db
) -> ProjectAgentChannel:
    """Make the project run the agents' testing revisions (`candidate`) or production (`stable`)."""
    ctx.require(Permission.PROJECT_WRITE)
    organisation = await session.get(Organization, ctx.project.org_id)
    assert organisation is not None
    before = ctx.project.agent_channel
    ctx.project.agent_channel = body.channel
    await session.flush()
    await record(
        session,
        ctx.principal,
        "project.agent_channel",
        org_id=organisation.id,
        target_type="project",
        target_id=ctx.project.id,
        before=before,
        after=body.channel,
    )
    return ProjectAgentChannel(channel=body.channel)
