# SPDX-License-Identifier: Apache-2.0
"""Forks, watchers, stars and the inbox items an agent's evolutions raise (ADR 0152).

Watching and starring are a person's (a personal token acts as its person); the system and an
outpost have none.
An event is written for every watcher at the moment it happens, and **read through the reader's
rights**: an item whose agent (or, for a fork, whose fork) the reader may no longer see is neither
listed nor counted, so losing access stops the stream without a second table to keep in step.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from choregos_core import utcnow
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.base import uuid7
from ..db.models import (
    Agent,
    AgentNotification,
    AgentStar,
    AgentVersion,
    AgentWatch,
    Organization,
    Project,
    User,
)
from ..errors import forbidden
from ..rbac import Principal
from ..schemas import (
    AgentForkDto,
    AgentNotificationDto,
    AgentNotificationList,
    AgentSocial,
    WatchLevel,
)
from .agent_governance import visible_clause

#: What a `releases` watcher is told; every other kind needs the `all` level.
RELEASE_KINDS = frozenset(
    {
        "version_published",
        "production_moved",
        "candidate_started",
        "candidate_promoted",
        "candidate_withdrawn",
        "rolled_back",
    }
)
LIMIT = 100


def require_person(principal: Principal) -> str:
    """The user id of a person; watching and starring are not for the system or an outpost."""
    if principal.kind != "user":
        raise forbidden(
            "watching and starring an agent are a person's gestures, not a system's or an outpost's"
        )
    return principal.user_id


# ───────────────────────────── forks ─────────────────────────────


async def visible_forks(
    session: AsyncSession, principal: Principal, organisation: Organization, agent: Agent
) -> list[Agent]:
    """The forks of `agent` the caller may see, newest first: the list's own visibility rule."""
    rows = await session.execute(
        select(Agent)
        .where(
            Agent.org_id == organisation.id,
            Agent.forked_from_agent_id == agent.id,
            await visible_clause(session, principal, organisation),
        )
        .order_by(Agent.created_at.desc(), Agent.id)
    )
    return list(rows.scalars())


async def fork_dtos(session: AsyncSession, forks: Iterable[Agent]) -> list[AgentForkDto]:
    forks = list(forks)
    if not forks:
        return []
    ids = [fork.id for fork in forks]
    latest = dict(
        (
            await session.execute(
                select(AgentVersion.agent_id, func.max(AgentVersion.version))
                .where(AgentVersion.agent_id.in_(ids))
                .group_by(AgentVersion.agent_id)
            )
        )
        .tuples()
        .all()
    )
    projects = dict(
        (
            await session.execute(
                select(Project.id, Project.slug).where(
                    Project.id.in_([f.project_id for f in forks if f.project_id])
                )
            )
        )
        .tuples()
        .all()
    )
    owners = dict(
        (
            await session.execute(
                select(User.id, User.email).where(User.id.in_([f.owner_id for f in forks if f.owner_id]))
            )
        )
        .tuples()
        .all()
    )
    return [
        AgentForkDto(
            slug=fork.slug,
            display_name=fork.display_name,
            scope=fork.scope,
            project=projects.get(fork.project_id) if fork.project_id else None,
            owner=owners.get(fork.owner_id) if fork.owner_id else None,
            created_at=fork.created_at,
            forked_from_version=fork.forked_from_version,
            latest_version=latest.get(fork.id, 0),
            diverged=latest.get(fork.id, 0) > 1,
            lifecycle=fork.lifecycle or "active",
        )
        for fork in forks
    ]


# ───────────────────────────── watching and starring ─────────────────────────────


async def social_of(
    session: AsyncSession, principal: Principal, organisation: Organization, agent: Agent
) -> AgentSocial:
    watchers = (
        await session.execute(
            select(func.count()).select_from(AgentWatch).where(AgentWatch.agent_id == agent.id)
        )
    ).scalar_one()
    stars = (
        await session.execute(
            select(func.count()).select_from(AgentStar).where(AgentStar.agent_id == agent.id)
        )
    ).scalar_one()
    mine_watch: str | None = None
    mine_star = False
    if principal.kind == "user":
        mine_watch = (
            await session.execute(
                select(AgentWatch.level).where(
                    AgentWatch.agent_id == agent.id, AgentWatch.user_id == principal.user_id
                )
            )
        ).scalar_one_or_none()
        mine_star = (
            await session.execute(
                select(AgentStar.id).where(
                    AgentStar.agent_id == agent.id, AgentStar.user_id == principal.user_id
                )
            )
        ).first() is not None
    return AgentSocial(
        watchers=watchers,
        stars=stars,
        forks=len(await visible_forks(session, principal, organisation, agent)),
        watching=mine_watch,
        starred_by_me=mine_star,
    )


async def watch(session: AsyncSession, principal: Principal, agent: Agent, level: WatchLevel) -> bool:
    """Starts (or retunes) the caller's watch. True when it is a new watch."""
    user_id = require_person(principal)
    row = (
        await session.execute(
            select(AgentWatch).where(AgentWatch.agent_id == agent.id, AgentWatch.user_id == user_id)
        )
    ).scalar_one_or_none()
    if row is not None:
        row.level = level
        await session.flush()
        return False
    session.add(AgentWatch(org_id=agent.org_id, agent_id=agent.id, user_id=user_id, level=level))
    await session.flush()
    return True


async def unwatch(session: AsyncSession, principal: Principal, agent: Agent) -> bool:
    user_id = require_person(principal)
    gone = await session.execute(
        delete(AgentWatch).where(AgentWatch.agent_id == agent.id, AgentWatch.user_id == user_id)
    )
    return bool(gone.rowcount)  # type: ignore[attr-defined]


async def star(session: AsyncSession, principal: Principal, agent: Agent) -> bool:
    user_id = require_person(principal)
    exists = (
        await session.execute(
            select(AgentStar.id).where(AgentStar.agent_id == agent.id, AgentStar.user_id == user_id)
        )
    ).first()
    if exists is not None:
        return False
    session.add(AgentStar(org_id=agent.org_id, agent_id=agent.id, user_id=user_id))
    await session.flush()
    return True


async def unstar(session: AsyncSession, principal: Principal, agent: Agent) -> bool:
    user_id = require_person(principal)
    gone = await session.execute(
        delete(AgentStar).where(AgentStar.agent_id == agent.id, AgentStar.user_id == user_id)
    )
    return bool(gone.rowcount)  # type: ignore[attr-defined]


async def auto_watch(session: AsyncSession, agent: Agent, user_id: str | None = None) -> None:
    """The owner of an agent watches it, until they say otherwise (a new agent, a fork, a transfer)."""
    user_id = user_id or agent.owner_id
    if user_id is None:
        return
    exists = (
        await session.execute(
            select(AgentWatch.id).where(AgentWatch.agent_id == agent.id, AgentWatch.user_id == user_id)
        )
    ).first()
    if exists is None:
        session.add(AgentWatch(org_id=agent.org_id, agent_id=agent.id, user_id=user_id, level="all"))
        await session.flush()


# ───────────────────────────── what watching delivers ─────────────────────────────


async def notify(
    session: AsyncSession,
    agent: Agent,
    kind: str,
    actor: Principal | None,
    *,
    version: int | None = None,
    previous_version: int | None = None,
    related: Agent | None = None,
    **detail: Any,
) -> int:
    """One inbox item per watcher of `agent`, except whoever did it. The item is written for everyone
    who watches; who may read it is decided when it is read. Returns how many were written."""
    query = select(AgentWatch.user_id).where(AgentWatch.agent_id == agent.id)
    if kind not in RELEASE_KINDS:
        query = query.where(AgentWatch.level == "all")
    if actor is not None:
        query = query.where(AgentWatch.user_id != actor.user_id)
    recipients = list((await session.execute(query)).scalars())
    if not recipients:
        return 0
    now = utcnow()
    # A Core insert: no `RETURNING`, which the recipient-only read policies would refuse the actor.
    await session.execute(
        insert(AgentNotification),
        [
            {
                "id": uuid7(),
                "org_id": agent.org_id,
                "user_id": user_id,
                "agent_id": agent.id,
                "related_agent_id": related.id if related else None,
                "kind": kind,
                "actor": actor.email if actor else None,
                "detail": {"version": version, "previous_version": previous_version, **detail},
                "created_at": now,
                "updated_at": now,
                "read_at": None,
            }
            for user_id in recipients
        ],
    )
    return len(recipients)


async def _readable(
    session: AsyncSession, principal: Principal, organisation: Organization, ids: set[str]
) -> set[str]:
    if not ids:
        return set()
    rows = await session.execute(
        select(Agent.id).where(
            Agent.org_id == organisation.id,
            Agent.id.in_(ids),
            await visible_clause(session, principal, organisation),
        )
    )
    return set(rows.scalars())


async def inbox_items(
    session: AsyncSession,
    principal: Principal,
    organisation: Organization,
    *,
    unread_only: bool = False,
    limit: int = LIMIT,
) -> AgentNotificationList:
    """The caller's items that they may still read, newest first, and how many are unread."""
    user_id = require_person(principal)
    query = (
        select(AgentNotification)
        .where(AgentNotification.org_id == organisation.id, AgentNotification.user_id == user_id)
        .order_by(AgentNotification.created_at.desc(), AgentNotification.id.desc())
        .limit(1000)
    )
    rows = list((await session.execute(query)).scalars())
    wanted = {r.agent_id for r in rows} | {r.related_agent_id for r in rows if r.related_agent_id}
    readable = await _readable(session, principal, organisation, wanted)
    visible = [
        r
        for r in rows
        if r.agent_id in readable and (r.related_agent_id is None or r.related_agent_id in readable)
    ]
    slugs = dict(
        (await session.execute(select(Agent.id, Agent.slug).where(Agent.id.in_(wanted or {""}))))
        .tuples()
        .all()
    )
    unread = sum(1 for r in visible if r.read_at is None)
    shown = [r for r in visible if r.read_at is None] if unread_only else visible
    return AgentNotificationList(
        items=[
            AgentNotificationDto(
                id=r.id,
                kind=r.kind,
                agent=slugs[r.agent_id],
                fork=slugs.get(r.related_agent_id) if r.related_agent_id else None,
                actor=r.actor,
                version=(r.detail or {}).get("version"),
                previous_version=(r.detail or {}).get("previous_version"),
                detail=(r.detail or {}).get("move") or (r.detail or {}).get("to"),
                created_at=r.created_at,
                read_at=r.read_at,
            )
            for r in shown[:limit]
        ],
        unread=unread,
    )


async def mark_read(
    session: AsyncSession, principal: Principal, organisation: Organization, item_id: str | None
) -> int:
    """Marks one item (or, with no id, all of them) read. Only the caller's own."""
    user_id = require_person(principal)
    statement = (
        update(AgentNotification)
        .where(
            AgentNotification.org_id == organisation.id,
            AgentNotification.user_id == user_id,
            AgentNotification.read_at.is_(None),
        )
        .values(read_at=utcnow())
    )
    if item_id is not None:
        statement = statement.where(AgentNotification.id == item_id)
    done = await session.execute(statement.execution_options(synchronize_session=False))
    return int(done.rowcount)  # type: ignore[attr-defined]
