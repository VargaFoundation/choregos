# SPDX-License-Identifier: Apache-2.0
"""Catalogue rights: who sees, installs, configures and enables an MCP server or a connector (ADR 0074).

One function decides, the routers ask it, the DTOs carry its answers as `can` and `cannot_reason`:
the console never guesses a right from a role (same pattern as ADR 0061, `agent_governance`).

| Gesture                                   | Organisation admin | Project owner of P | Member (any role) |
| :---------------------------------------- | :----------------- | :----------------- | :---------------- |
| SEE the catalogue and its instances       | yes                | yes                | yes               |
| INSTALL an organisation-scope entry       | yes                | no                 | no                |
| CONFIGURE a project-scope type in P       | yes                | yes, in P          | no                |
| SET_AVAILABILITY of an org connector      | yes                | no                 | no                |
| ENABLE / disable an org connector for P   | yes, any project   | yes, in P          | no                |

"Member" is any role in the organisation or in one of its projects. USE is not a catalogue gesture:
a run of P calls an operation only when the connector is enabled for P (`connector_enabled_for`),
the effective policy is not forbidden, the groups match and the agent version selects it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import OrgConnector, OrgConnectorProject, Project
from ..rbac import Permission, Principal
from .agent_governance import is_member

ALL_PROJECTS = "all_projects"
SELECTED_PROJECTS = "selected_projects"


class CatalogueAction(StrEnum):
    SEE = "see"
    INSTALL = "install"
    CONFIGURE = "configure"
    SET_AVAILABILITY = "set_availability"
    ENABLE = "enable"


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    reason: str | None = None


_ALLOWED = Decision(True)


def decide(
    principal: Principal, org: str, action: CatalogueAction, *, project_slug: str | None = None
) -> Decision:
    """The answer to "may this person do this here", with the sentence that says why not."""
    if action is CatalogueAction.SEE:
        if is_member(principal, org):
            return _ALLOWED
        return Decision(False, f"the catalogue is visible to the members of {org}")
    if action in (CatalogueAction.INSTALL, CatalogueAction.SET_AVAILABILITY):
        if principal.can(Permission.TOOLS_GRANT, org):
            return _ALLOWED
        what = "installed" if action is CatalogueAction.INSTALL else "made available to projects"
        return Decision(False, f"connectors of {org} are {what} by its administrator")
    # CONFIGURE and ENABLE: the project's own lock.
    if project_slug is None:
        return Decision(False, "name the project")
    if principal.can(Permission.CONNECTOR_WRITE, org, project_slug):
        return _ALLOWED
    verb = "configures connectors" if action is CatalogueAction.CONFIGURE else "turns connectors on"
    return Decision(False, f"only the owner of {project_slug} or an administrator {verb} for it")


def projects_where(
    principal: Principal, org: str, action: CatalogueAction, visible_projects: list[str]
) -> list[str]:
    """The projects, among those the caller sees, where `decide` lets `action` through."""
    return [p for p in visible_projects if decide(principal, org, action, project_slug=p).allowed]


async def visible_projects(session: AsyncSession, principal: Principal, org_id: str, org: str) -> list[str]:
    """Slugs of the organisation's projects the caller may read (an organisation role: all)."""
    slugs = (await session.execute(select(Project.slug).where(Project.org_id == org_id))).scalars()
    return sorted(s for s in slugs if principal.can(Permission.PROJECT_READ, org, s))


async def connector_enabled_for(session: AsyncSession, connector: OrgConnector, project: Project) -> bool:
    """`all_projects`: true. `selected_projects`: true when a row turns it on for this project."""
    if connector.availability != SELECTED_PROJECTS:
        return True
    row = (
        await session.execute(
            select(OrgConnectorProject.id).where(
                OrgConnectorProject.connector_id == connector.id, OrgConnectorProject.project_id == project.id
            )
        )
    ).first()
    return row is not None


async def enabled_ids(session: AsyncSession, project: Project) -> set[str]:
    """The ids of the connectors turned on for this project (one query, for the broker loop)."""
    rows = (
        await session.execute(
            select(OrgConnectorProject.connector_id).where(OrgConnectorProject.project_id == project.id)
        )
    ).scalars()
    return set(rows)


async def enabled_projects_of(
    session: AsyncSession, connectors: list[OrgConnector], visible: list[str], org_id: str
) -> dict[str, list[str]]:
    """For each `selected_projects` connector id, the visible project slugs it is on for."""
    ids = [c.id for c in connectors if c.availability == SELECTED_PROJECTS]
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(OrgConnectorProject.connector_id, Project.slug)
            .join(Project, Project.id == OrgConnectorProject.project_id)
            .where(OrgConnectorProject.connector_id.in_(ids), Project.org_id == org_id)
        )
    ).all()
    seen = set(visible)
    out: dict[str, list[str]] = {i: [] for i in ids}
    for connector_id, slug in rows:
        if slug in seen:
            out[connector_id].append(slug)
    return {k: sorted(v) for k, v in out.items()}
