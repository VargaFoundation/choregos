# SPDX-License-Identifier: Apache-2.0
"""An agent's forks, its watchers and its stars, and the inbox items watching raises (ADR 0152).

Any member who may see an agent may watch or star it; what they may not see is a 404, like every
other agent route. The forks listed are the ones the caller may see.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Query

from ..audit import record
from ..db.models import Agent
from ..deps import Db, Me
from ..errors import not_found
from ..schemas import (
    AgentForkList,
    AgentNotificationList,
    AgentNotificationsRead,
    AgentSocial,
    AgentWatchPut,
)
from ..services import agent_social as social
from ..services.agent_access import load_visible_agent, require_agent_reader
from ..services.organisations import organisation_by_slug
from .agents import Slug

router = APIRouter(tags=["agents"])


@router.get("/orgs/{org}/agents/{slug}/forks", response_model=AgentForkList, operation_id="listAgentForks")
async def list_agent_forks(org: str, slug: Slug, session: Db, principal: Me) -> AgentForkList:
    """The forks of an agent that the caller may see, newest first: scope, owner, the version copied,
    its latest version, and whether it has been edited since (a second version exists)."""
    organisation, agent, _ = await load_visible_agent(session, principal, org, slug)
    forks = await social.visible_forks(session, principal, organisation, agent)
    return AgentForkList(items=await social.fork_dtos(session, forks))


async def _gesture(
    session: Db,
    principal: Me,
    org: str,
    slug: str,
    action: str,
    apply: Callable[[Agent], Awaitable[object]],
    **payload: object,
) -> AgentSocial:
    """Load the visible agent, do the gesture, audit it, and answer with the new counters."""
    organisation, agent, _ = await load_visible_agent(session, principal, org, slug)
    await apply(agent)
    await record(
        session,
        principal,
        action,
        org_id=organisation.id,
        target_type="agent",
        target_id=agent.id,
        agent=agent.slug,
        **payload,
    )
    return await social.social_of(session, principal, organisation, agent)


@router.put("/orgs/{org}/agents/{slug}/watch", response_model=AgentSocial, operation_id="watchAgent")
async def watch_agent(
    org: str, slug: Slug, session: Db, principal: Me, body: AgentWatchPut | None = None
) -> AgentSocial:
    """Follow the agent's evolutions: a new version, production moving, a candidate starting, being
    promoted or withdrawn, a rollback, a fork, its deprecation or retirement reach the caller's inbox.
    `level: releases` leaves out forks and lifecycle changes. Idempotent; a second call retunes."""
    level = (body or AgentWatchPut()).level
    return await _gesture(
        session,
        principal,
        org,
        slug,
        "agent.watch",
        lambda a: social.watch(session, principal, a, level),
        level=level,
    )


@router.delete("/orgs/{org}/agents/{slug}/watch", response_model=AgentSocial, operation_id="unwatchAgent")
async def unwatch_agent(org: str, slug: Slug, session: Db, principal: Me) -> AgentSocial:
    """Stop following. Idempotent. The owner who unwatches stays unwatched."""
    return await _gesture(
        session, principal, org, slug, "agent.unwatch", lambda a: social.unwatch(session, principal, a)
    )


@router.put("/orgs/{org}/agents/{slug}/star", response_model=AgentSocial, operation_id="starAgent")
async def star_agent(org: str, slug: Slug, session: Db, principal: Me) -> AgentSocial:
    """Star the agent: a signal of use and the caller's shortlist (the list filters on it). Idempotent."""
    return await _gesture(
        session, principal, org, slug, "agent.star", lambda a: social.star(session, principal, a)
    )


@router.delete("/orgs/{org}/agents/{slug}/star", response_model=AgentSocial, operation_id="unstarAgent")
async def unstar_agent(org: str, slug: Slug, session: Db, principal: Me) -> AgentSocial:
    return await _gesture(
        session, principal, org, slug, "agent.unstar", lambda a: social.unstar(session, principal, a)
    )


@router.get(
    "/orgs/{org}/agent-notifications",
    response_model=AgentNotificationList,
    operation_id="listAgentNotifications",
)
async def list_agent_notifications(
    org: str,
    session: Db,
    principal: Me,
    unread: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = social.LIMIT,
) -> AgentNotificationList:
    """What happened to the agents the caller watches, newest first. An item about an agent (or a fork)
    the caller can no longer see is not listed and not counted."""
    require_agent_reader(principal, org)
    organisation = await organisation_by_slug(session, org)
    return await social.inbox_items(session, principal, organisation, unread_only=unread, limit=limit)


@router.post(
    "/orgs/{org}/agent-notifications/read",
    response_model=AgentNotificationsRead,
    operation_id="readAllAgentNotifications",
)
async def read_all_agent_notifications(org: str, session: Db, principal: Me) -> AgentNotificationsRead:
    require_agent_reader(principal, org)
    organisation = await organisation_by_slug(session, org)
    return AgentNotificationsRead(updated=await social.mark_read(session, principal, organisation, None))


@router.post(
    "/orgs/{org}/agent-notifications/{item_id}/read",
    response_model=AgentNotificationsRead,
    operation_id="readAgentNotification",
)
async def read_agent_notification(
    org: str, item_id: str, session: Db, principal: Me
) -> AgentNotificationsRead:
    require_agent_reader(principal, org)
    organisation = await organisation_by_slug(session, org)
    updated = await social.mark_read(session, principal, organisation, item_id)
    if updated == 0:
        raise not_found("Agent notification", item_id)
    return AgentNotificationsRead(updated=updated)
