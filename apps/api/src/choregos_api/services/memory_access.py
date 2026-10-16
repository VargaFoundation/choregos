# SPDX-License-Identifier: Apache-2.0
"""Who may see a named memory, which runs reach it, and which adapter serves it (S40-11, ADR 0075).

Row-level security keeps a memory inside its organisation; inside it:

- a **global** memory is curated by an organisation administrator (`agent:manage`). Its `reach` says
  who sees it and whose runs read it: `org` (every member, every project) or `projects` (members of a
  listed project, runs of a listed project). Administrators see all of them.
- a **project** memory belongs to one project: its members see it, its runs read it, its owners
  (`project:write`) create and archive it.

What a caller may not see does not exist for it (404 on a route, "unknown memory" at publication).
"""

from __future__ import annotations

from typing import Any

from choregos_core.memory_bindings import RESERVED_SLUGS
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Connector, MemoryStore, Organization, OrgConnector
from ..errors import unprocessable
from ..rbac import Permission, Principal
from ..schemas.agents import AgentSpec


def can_manage(principal: Principal, org: str) -> bool:
    return principal.can(Permission.AGENT_MANAGE, org)


def visible_to(principal: Principal, org: str, store: Any) -> bool:
    """Whether a human (or a token acting for one) may see this memory."""
    if can_manage(principal, org):
        return True
    if store.scope == "project":
        return any(principal.can(Permission.PROJECT_READ, org, project) for project in store.projects or [])
    if store.reach == "org":
        # Every member of the organisation, including one who holds only a project role: their
        # project's runs read it.
        return org in principal.org_roles or any(key.startswith(f"{org}/") for key in principal.project_roles)
    return any(principal.can(Permission.PROJECT_READ, org, project) for project in store.projects or [])


def reaches_project(store: Any, project_slug: str) -> bool:
    """Whether a run of this project may read this memory (and the memory is usable at all)."""
    if store.status != "active":
        return False
    if store.scope == "project":
        return project_slug in (store.projects or [])
    if store.reach == "org":
        return True
    return project_slug in (store.projects or [])


def adapter_config(row: Any | None, *, tenant: str, org: str) -> tuple[str, dict[str, Any]]:
    """The adapter type and its config from a `memory` connector row; Ecphoria by default."""
    type_name = row.type if row is not None else "ecphoria"
    config = dict(row.config or {}) if row is not None else {}
    return type_name, {**config, "tenant": tenant, "org": org}


async def project_adapter(session: AsyncSession, project_id: str, project_slug: str, org: str) -> Any:
    """The memory adapter of a project: its `memory` connector, as the project configured it."""
    from choregos_adapters import build

    row = (
        await session.execute(
            select(Connector).where(Connector.project_id == project_id, Connector.kind == "memory")
        )
    ).scalar_one_or_none()
    type_name, config = adapter_config(row, tenant=project_slug, org=org)
    return build("memory", type_name, config)


async def global_adapter(session: AsyncSession, organisation: Organization) -> Any:
    """The adapter of the organisation's global memories: its `memory` connector when it declared one,
    else the deployment default (what a project without a memory connector gets)."""
    from choregos_adapters import build

    row = (
        (
            await session.execute(
                select(OrgConnector)
                .where(OrgConnector.org_id == organisation.id, OrgConnector.kind == "memory")
                .order_by(OrgConnector.name)
            )
        )
        .scalars()
        .first()
    )
    type_name, config = adapter_config(row, tenant=f"org:{organisation.slug}", org=organisation.slug)
    return build("memory", type_name, config)


async def stores_of(session: AsyncSession, organisation: Organization) -> list[MemoryStore]:
    return list(
        (
            await session.execute(
                select(MemoryStore).where(MemoryStore.org_id == organisation.id).order_by(MemoryStore.slug)
            )
        ).scalars()
    )


async def check_bindings_at_publish(
    session: AsyncSession,
    principal: Principal,
    organisation: Organization,
    spec: AgentSpec,
    *,
    project_slug: str | None = None,
) -> None:
    """Refuse (422) a version that binds a global memory the publisher does not see, or — for a project
    agent — a named project memory its project does not have. One message per problem; an invisible
    memory is "unknown", never "forbidden", so its existence is not revealed.

    The project references of any other agent are relative: checked when a run resolves them.
    """
    if not spec.memories:
        return
    stores = await stores_of(session, organisation)
    project = None
    if project_slug is not None:
        project = next(iter(await _projects(session, organisation, project_slug)), None)
    errors: list[dict[str, Any]] = []
    for index, binding in enumerate(spec.memories):
        loc = ["memories", index]
        if binding.scope == "global":
            found = next((s for s in stores if s.scope == "global" and s.slug == binding.slug), None)
            if found is None or not visible_to(principal, organisation.slug, found):
                errors.append({"loc": loc, "msg": f"unknown memory `{binding.slug}`"})
            elif found.status != "active":
                errors.append({"loc": loc, "msg": f"memory `{binding.slug}` is archived"})
        elif project_slug is not None and binding.slug not in RESERVED_SLUGS:
            found = next(
                (
                    s
                    for s in stores
                    if s.scope == "project"
                    and project is not None
                    and s.owner_key == project.id
                    and s.slug == binding.slug
                ),
                None,
            )
            if found is None or found.status != "active":
                errors.append(
                    {"loc": loc, "msg": f"unknown memory `{binding.slug}` in project {project_slug}"}
                )
    if errors:
        raise unprocessable("the version binds memories that do not exist", errors)


async def _projects(session: AsyncSession, organisation: Organization, slug: str) -> list[Any]:
    from ..db.models import Project

    return list(
        (
            await session.execute(
                select(Project).where(Project.org_id == organisation.id, Project.slug == slug)
            )
        ).scalars()
    )


async def project_namespace(
    session: AsyncSession, project: Any, memory: str | None, agent: str | None
) -> str:
    """The namespace a review of a project's memory reads (`?memory=`, `?agent=`).

    Absent or `shared`: the project's shared memory (as before S40-11). `own`: the memory of the agent
    named by `agent`. Anything else: a named memory of this project (404 when it has none of that name).
    """
    from choregos_core.memory_scope import project_store_namespace

    from ..errors import not_found

    if memory is None or memory == "shared":
        return str(project.slug)
    if memory == "own":
        if not agent:
            raise unprocessable("`memory=own` needs `agent=<slug>`: whose own memory?")
        return f"{project.slug}#agent:{agent}"
    store = (
        await session.execute(
            select(MemoryStore).where(
                MemoryStore.org_id == project.org_id,
                MemoryStore.owner_key == str(project.id),
                MemoryStore.slug == memory,
            )
        )
    ).scalar_one_or_none()
    if store is None:
        raise not_found("Memory", f"{project.slug}/{memory}")
    return project_store_namespace(str(project.slug), memory)
