# SPDX-License-Identifier: Apache-2.0
"""Named memories, global and project (S40-11, ADR 0075).

A global memory is the organisation's: curated by an administrator (`agent:manage`), with a reach. A
project memory is a project's: created and archived by its owners (`project:write`), read by its runs
only. `shared` and `own` — every project's implicit memories — are listed with them but are never rows.
What the caller may not see does not exist for it (404).
"""

from __future__ import annotations

from typing import Annotated, Any

from choregos_core.memory_bindings import OWN, RESERVED_SLUGS, SHARED
from choregos_core.memory_scope import global_store_namespace
from fastapi import APIRouter, Path, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import Agent, AgentVersion, MemoryStore, Organization, Project
from ..deps import Db, Me, ProjectCtx
from ..errors import conflict, forbidden, not_found, unprocessable
from ..rbac import Permission
from ..schemas import (
    AgentSpec,
    MemoryDecision,
    MemoryDto,
    MemoryStoreCreate,
    MemoryStoreDto,
    MemoryStorePatch,
    MemoryStoreUser,
    ProjectMemoryCreate,
)
from ..services.agent_governance import visible_clause
from ..services.memory_access import (
    can_manage,
    global_adapter,
    reaches_project,
    stores_of,
    visible_to,
)
from .memory import to_dto as memory_dto

router = APIRouter(tags=["memory"])

Slug = Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]{1,62}$")]


async def _organisation(session: AsyncSession, slug: str) -> Organization:
    organisation = (
        await session.execute(select(Organization).where(Organization.slug == slug))
    ).scalar_one_or_none()
    if organisation is None:
        raise not_found("Organisation", slug)
    return organisation


def _member(principal: Me, org: str) -> None:
    if principal.org_roles.get(org) is None and not any(
        key.startswith(f"{org}/") for key in principal.project_roles
    ):
        raise forbidden(f"reading the memories of {org} requires being a member of it")


def _manage(principal: Me, org: str) -> None:
    if not can_manage(principal, org):
        raise forbidden("curating an organisation memory needs the org_admin role")


async def _known_projects(session: AsyncSession, organisation: Organization, slugs: list[str]) -> None:
    if not slugs:
        return
    known = set(
        (
            await session.execute(
                select(Project.slug).where(Project.org_id == organisation.id, Project.slug.in_(slugs))
            )
        ).scalars()
    )
    if unknown := sorted(set(slugs) - known):
        raise unprocessable(f"unknown project(s) in {organisation.slug}: {', '.join(unknown)}")


def _not_reserved(slug: str) -> None:
    if slug in RESERVED_SLUGS:
        raise unprocessable(f"`{slug}` is reserved for the implicit memories of every project")


async def _used_by(
    session: AsyncSession, principal: Me, organisation: Organization
) -> dict[tuple[str, str], list[MemoryStoreUser]]:
    """For each `(scope, slug)` the visible agents whose LATEST version binds it."""
    latest = (
        select(AgentVersion.agent_id, func.max(AgentVersion.version).label("version"))
        .where(AgentVersion.org_id == organisation.id)
        .group_by(AgentVersion.agent_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(Agent.slug, AgentVersion.version, AgentVersion.spec)
            .join(latest, (latest.c.agent_id == Agent.id))
            .join(
                AgentVersion,
                (AgentVersion.agent_id == Agent.id) & (AgentVersion.version == latest.c.version),
            )
            .where(Agent.org_id == organisation.id, await visible_clause(session, principal, organisation))
            .order_by(Agent.slug)
        )
    ).all()
    used: dict[tuple[str, str], list[MemoryStoreUser]] = {}
    for agent, version, raw in rows:
        for binding in AgentSpec.model_validate(raw).effective_memories():
            used.setdefault((binding.scope, binding.slug), []).append(
                MemoryStoreUser(agent=agent, version=int(version))
            )
    return used


def _dto(store: MemoryStore, used: dict[tuple[str, str], list[MemoryStoreUser]]) -> MemoryStoreDto:
    return MemoryStoreDto(
        slug=store.slug,
        display_name=store.display_name,
        description=store.description,
        scope=store.scope,
        project=(store.projects[0] if store.scope == "project" and store.projects else None),
        reach=store.reach,
        projects=list(store.projects or []),
        status=store.status,
        created_by=store.created_by,
        created_at=store.created_at,
        used_by=used.get((store.scope, store.slug), []),
    )


async def _global(
    session: AsyncSession, principal: Me, org: str, slug: str
) -> tuple[Organization, MemoryStore]:
    """The global memory, if the caller may see it; one it may not see does not exist for it."""
    organisation = await _organisation(session, org)
    store = (
        await session.execute(
            select(MemoryStore).where(
                MemoryStore.org_id == organisation.id,
                MemoryStore.owner_key == "org",
                MemoryStore.slug == slug,
            )
        )
    ).scalar_one_or_none()
    if store is None or not visible_to(principal, org, store):
        raise not_found("Memory", f"{org}/{slug}")
    return organisation, store


@router.get("/orgs/{org}/memories", response_model=list[MemoryStoreDto], operation_id="listMemories")
async def list_memories(
    org: str,
    session: Db,
    principal: Me,
    scope: Annotated[str | None, Query(pattern="^(global|project)$")] = None,
    project: Annotated[str | None, Query()] = None,
) -> list[MemoryStoreDto]:
    """The memories the caller sees: global ones with their reach, project ones of their projects."""
    _member(principal, org)
    organisation = await _organisation(session, org)
    used = await _used_by(session, principal, organisation)
    return [
        _dto(store, used)
        for store in await stores_of(session, organisation)
        if visible_to(principal, org, store)
        and (scope is None or store.scope == scope)
        and (project is None or project in (store.projects or []))
    ]


@router.post(
    "/orgs/{org}/memories",
    response_model=MemoryStoreDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="createGlobalMemory",
)
async def create_global_memory(
    org: str, body: MemoryStoreCreate, session: Db, principal: Me
) -> MemoryStoreDto:
    _manage(principal, org)
    _not_reserved(body.slug)
    organisation = await _organisation(session, org)
    if (
        await session.execute(
            select(MemoryStore.id).where(
                MemoryStore.org_id == organisation.id,
                MemoryStore.owner_key == "org",
                MemoryStore.slug == body.slug,
            )
        )
    ).first():
        raise conflict(f"global memory `{body.slug}` already exists")
    await _known_projects(session, organisation, body.projects)
    store = MemoryStore(
        org_id=organisation.id,
        slug=body.slug,
        display_name=body.display_name,
        description=body.description,
        scope="global",
        project_id=None,
        owner_key="org",
        reach=body.reach,
        projects=sorted(set(body.projects)) if body.reach == "projects" else [],
        status="active",
        created_by=principal.email,
    )
    session.add(store)
    await session.flush()
    await record(
        session,
        principal,
        "memory.create",
        org_id=organisation.id,
        target_type="memory_store",
        target_id=store.id,
        scope="global",
        reach=store.reach,
    )
    return _dto(store, {})


@router.get("/orgs/{org}/memories/{slug}", response_model=MemoryStoreDto, operation_id="getMemory")
async def get_memory(org: str, slug: Slug, session: Db, principal: Me) -> MemoryStoreDto:
    _member(principal, org)
    organisation, store = await _global(session, principal, org, slug)
    return _dto(store, await _used_by(session, principal, organisation))


@router.patch("/orgs/{org}/memories/{slug}", response_model=MemoryStoreDto, operation_id="updateMemory")
async def update_memory(
    org: str, slug: Slug, body: MemoryStorePatch, session: Db, principal: Me
) -> MemoryStoreDto:
    _manage(principal, org)
    organisation, store = await _global(session, principal, org, slug)
    changes = body.model_dump(exclude_unset=True)
    reach = changes.get("reach", store.reach)
    projects = changes.get("projects", store.projects) or []
    if reach == "projects" and not projects:
        raise unprocessable("reach `projects` names at least one project")
    await _known_projects(session, organisation, projects if reach == "projects" else [])
    for field in ("display_name", "description"):
        if field in changes and (changes[field] is not None or field == "description"):
            setattr(store, field, changes[field])
    archived = changes.get("status") == "archived" and store.status != "archived"
    if changes.get("status") is not None:
        store.status = changes["status"]
    store.reach = reach
    store.projects = sorted(set(projects)) if reach == "projects" else []
    await session.flush()
    await record(
        session,
        principal,
        "memory.archive" if archived else "memory.update",
        org_id=organisation.id,
        target_type="memory_store",
        target_id=store.id,
        changes=sorted(changes),
    )
    return _dto(store, await _used_by(session, principal, organisation))


@router.get(
    "/orgs/{org}/memories/{slug}/search", response_model=list[MemoryDto], operation_id="searchGlobalMemory"
)
async def search_global_memory(
    org: str,
    slug: Slug,
    session: Db,
    principal: Me,
    q: Annotated[str, Query()],
    k: Annotated[int, Query(le=100)] = 10,
) -> list[MemoryDto]:
    _member(principal, org)
    organisation, store = await _global(session, principal, org, slug)
    adapter = await global_adapter(session, organisation)
    return [memory_dto(m) for m in await adapter.search(global_store_namespace(org, store.slug), q, k)]


@router.get(
    "/orgs/{org}/memories/{slug}/pending",
    response_model=list[MemoryDto],
    operation_id="listGlobalPendingMemory",
)
async def list_global_pending(org: str, slug: Slug, session: Db, principal: Me) -> list[MemoryDto]:
    """What agents proposed to this memory, waiting for an administrator."""
    _manage(principal, org)
    organisation, store = await _global(session, principal, org, slug)
    adapter = await global_adapter(session, organisation)
    rows = await adapter.list_pending(global_store_namespace(org, store.slug))
    return [memory_dto(m) for m in rows]


@router.post("/orgs/{org}/memories/{slug}/pending", operation_id="decideGlobalPendingMemory")
async def decide_global_pending(
    org: str, slug: Slug, body: MemoryDecision, session: Db, principal: Me
) -> dict[str, str]:
    _manage(principal, org)
    organisation, store = await _global(session, principal, org, slug)
    adapter = await global_adapter(session, organisation)
    namespace = global_store_namespace(org, store.slug)
    if body.action == "accept":
        done = await adapter.accept_pending(namespace, body.id)
    else:
        done = (
            await adapter.reject_pending(namespace, body.id) if hasattr(adapter, "reject_pending") else True
        )
    await record(
        session,
        principal,
        f"memory.{body.action}",
        org_id=organisation.id,
        target_type="memory",
        target_id=body.id,
        reason=body.reason,
        memory_store=store.slug,
    )
    return {"status": "accepted" if done else "not_found"}


_IMPLICIT: dict[str, tuple[str, str]] = {
    SHARED: ("Shared memory", "What the project knows: read and written by the runs that use it."),
    OWN: ("Own memory", "What an agent learnt in this project: read by that agent only."),
}


@router.get(
    "/projects/{id}/memories", response_model=list[MemoryStoreDto], operation_id="listProjectMemories"
)
async def list_project_memories(ctx: ProjectCtx, session: Db) -> list[MemoryStoreDto]:
    """What a run of this project may bind: the implicit `shared` and `own`, the project's named
    memories, and the global ones that reach it."""
    organisation = await _organisation(session, ctx.org_slug)
    used = await _used_by(session, ctx.principal, organisation)
    implicit = [
        MemoryStoreDto(
            slug=slug,
            display_name=name,
            description=description,
            scope="project",
            project=ctx.slug,
            implicit=True,
            used_by=used.get(("project", slug), []),
        )
        for slug, (name, description) in _IMPLICIT.items()
    ]
    named = [
        _dto(store, used)
        for store in await stores_of(session, organisation)
        if (store.scope == "project" and store.owner_key == ctx.id)
        or (store.scope == "global" and reaches_project(store, ctx.slug))
    ]
    return [*implicit, *named]


@router.post(
    "/projects/{id}/memories",
    response_model=MemoryStoreDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="createProjectMemory",
)
async def create_project_memory(ctx: ProjectCtx, body: ProjectMemoryCreate, session: Db) -> MemoryStoreDto:
    """A named memory of this project, read by its runs only. `project:write`."""
    ctx.require(Permission.PROJECT_WRITE)
    _not_reserved(body.slug)
    if (
        await session.execute(
            select(MemoryStore.id).where(
                MemoryStore.org_id == ctx.project.org_id,
                MemoryStore.owner_key == ctx.id,
                MemoryStore.slug == body.slug,
            )
        )
    ).first():
        raise conflict(f"memory `{body.slug}` already exists in {ctx.slug}")
    store = MemoryStore(
        org_id=ctx.project.org_id,
        slug=body.slug,
        display_name=body.display_name,
        description=body.description,
        scope="project",
        project_id=ctx.id,
        owner_key=ctx.id,
        reach=None,
        projects=[ctx.slug],
        status="active",
        created_by=ctx.principal.email,
    )
    session.add(store)
    await session.flush()
    await record(
        session,
        ctx.principal,
        "memory.create",
        org_id=ctx.project.org_id,
        target_type="memory_store",
        target_id=store.id,
        scope="project",
        project=ctx.slug,
    )
    return _dto(store, {})


@router.patch(
    "/projects/{id}/memories/{slug}",
    response_model=MemoryStoreDto,
    operation_id="updateProjectMemory",
)
async def update_project_memory(
    ctx: ProjectCtx, slug: Slug, body: MemoryStorePatch, session: Db
) -> MemoryStoreDto:
    """Rename or archive a named memory of this project. `project:write`; its reach is not a thing."""
    ctx.require(Permission.PROJECT_WRITE)
    if body.reach is not None or body.projects is not None:
        raise unprocessable("a project memory has no reach: it is read by its project's runs only")
    store = (
        await session.execute(
            select(MemoryStore).where(
                MemoryStore.org_id == ctx.project.org_id,
                MemoryStore.owner_key == ctx.id,
                MemoryStore.slug == slug,
            )
        )
    ).scalar_one_or_none()
    if store is None:
        raise not_found("Memory", f"{ctx.slug}/{slug}")
    changes = body.model_dump(exclude_unset=True)
    if changes.get("display_name") is not None:
        store.display_name = changes["display_name"]
    if "description" in changes:
        store.description = changes["description"]
    archived = changes.get("status") == "archived" and store.status != "archived"
    if changes.get("status") is not None:
        store.status = changes["status"]
    await session.flush()
    await record(
        session,
        ctx.principal,
        "memory.archive" if archived else "memory.update",
        org_id=ctx.project.org_id,
        target_type="memory_store",
        target_id=store.id,
        changes=sorted(changes),
    )
    used: dict[Any, Any] = await _used_by(session, ctx.principal, await _organisation(session, ctx.org_slug))
    return _dto(store, used)
