# SPDX-License-Identifier: Apache-2.0
"""The connector catalogue: the registry's connector types, by family, with what each means in an
organisation and what the caller may do with it (ADR 0074).

Built from the adapter registry (`choregos_adapters.offered_connector_types()`: fakes, deprecated
names and, in staging and prod, the demonstrations are already left out), so a type a plugin
registers appears without a line here. Platform plumbing
(`runtime`, `gateway`, `memory`) is how the platform runs, not something a person installs: it is not
an entry. Nothing secret is in an entry: field names, never values.
"""

from __future__ import annotations

from choregos_adapters import offered_connector_types
from choregos_core.dsl.exigences import ORDRE
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import Connector, Organization, OrgConnector, Project
from ..rbac import Principal
from ..schemas import (
    CataloguePermissions,
    ConnectorCatalogueEntryDto,
    ConnectorEnablement,
)
from .catalogue_rights import (
    SELECTED_PROJECTS,
    CatalogueAction,
    decide,
    enabled_projects_of,
    projects_where,
    visible_projects,
)

PLATFORM_KINDS = frozenset({"runtime", "gateway", "memory"})
#: The kinds a project configures for itself (its delivery connectors): tracker, scm, ci, cd, notify.
PROJECT_KINDS = frozenset(ORDRE) - PLATFORM_KINDS

FAMILIES: dict[str, str] = {
    "scm": "Source code",
    "tracker": "Work trackers",
    "ci": "CI",
    "cd": "Deployment",
    "documents": "Documents",
    "identity": "Directory",
    "mdm": "Device fleet",
    "shipping": "Carriers",
    "access_control": "Physical access",
    "firewall": "Network operations",
    "network_policy": "Network operations",
    "web_server": "Network operations",
    "notify": "Notifications",
    "web_search": "Web search",
    "mcp": "MCP servers",
}

#: The order the console lists the families in; the others follow alphabetically.
FAMILY_ORDER = (
    "Source code",
    "Work trackers",
    "CI",
    "Deployment",
    "Documents",
    "Directory",
    "Network operations",
    "Notifications",
    "Web search",
    "MCP servers",
)


def family_of(kind: str) -> str:
    return FAMILIES.get(kind, "Other")


async def enablement_by_connector(
    session: AsyncSession, principal: Principal, organisation: Organization
) -> dict[str, ConnectorEnablement]:
    """Per organisation connector name: its availability and, among the projects the caller sees,
    where it is on (empty with `all_projects`)."""
    connectors = list(
        (await session.execute(select(OrgConnector).where(OrgConnector.org_id == organisation.id)))
        .scalars()
        .all()
    )
    visible = await visible_projects(session, principal, organisation.id, organisation.slug)
    enabled = await enabled_projects_of(session, connectors, visible, organisation.id)
    return {
        c.name: ConnectorEnablement(
            connector=c.name,
            availability=c.availability,
            enabled_projects=enabled.get(c.id, []),
        )
        for c in connectors
    }


def _matches(entry: ConnectorCatalogueEntryDto, q: str) -> bool:
    needle = q.lower().strip()
    return not needle or needle in " ".join([entry.kind, entry.type, entry.display, entry.family]).lower()


async def list_entries(
    session: AsyncSession,
    principal: Principal,
    organisation: Organization,
    *,
    q: str = "",
    family: str | None = None,
    scope: str | None = None,
) -> list[ConnectorCatalogueEntryDto]:
    org = organisation.slug
    visible = await visible_projects(session, principal, organisation.id, org)
    instances = list(
        (await session.execute(select(OrgConnector).where(OrgConnector.org_id == organisation.id)))
        .scalars()
        .all()
    )
    enablement = await enablement_by_connector(session, principal, organisation)
    project_rows = (
        await session.execute(
            select(Connector.kind, Connector.type, Project.slug)
            .join(Project, Project.id == Connector.project_id)
            .where(Project.org_id == organisation.id)
        )
    ).all()
    seen = set(visible)
    install = decide(principal, org, CatalogueAction.INSTALL)
    configure = projects_where(principal, org, CatalogueAction.CONFIGURE, visible)
    enable = projects_where(principal, org, CatalogueAction.ENABLE, visible)

    entries: list[ConnectorCatalogueEntryDto] = []
    for kind, type_name, spec in offered_connector_types(get_settings().env):
        if kind in PLATFORM_KINDS:
            continue
        project_scope = kind in PROJECT_KINDS
        here = [i for i in instances if (i.kind, i.type) == (kind, type_name)]
        configured = sorted({s for k, t, s in project_rows if (k, t) == (kind, type_name) and s in seen})
        selected = any(i.availability == SELECTED_PROJECTS for i in here)
        if project_scope:
            can = CataloguePermissions(install=False, configure_projects=configure, enable_projects=[])
            reason = (
                None
                if configure
                else "only the owner of a project or an administrator configures its connectors"
            )
        else:
            can = CataloguePermissions(
                install=install.allowed,
                configure_projects=[],
                enable_projects=enable if selected else [],
            )
            reason = None if install.allowed else install.reason
        entries.append(
            ConnectorCatalogueEntryDto(
                kind=kind,
                type=type_name,
                display=spec.display,
                family=family_of(kind),
                scope="project" if project_scope else "organisation",
                capabilities=list(spec.capabilities),
                secret_fields=list(spec.secret_fields),
                pooled_fields=list(spec.pooled_fields),
                operations=len(spec.operations),
                installed_as=sorted(i.name for i in here),
                configured_in=configured,
                enablement=[enablement[i.name] for i in sorted(here, key=lambda i: i.name)],
                can=can,
                cannot_reason=reason,
            )
        )
    entries = [
        e
        for e in entries
        if _matches(e, q) and (family is None or e.family == family) and (scope is None or e.scope == scope)
    ]
    rank = {name: i for i, name in enumerate(FAMILY_ORDER)}
    entries.sort(key=lambda e: (rank.get(e.family, len(rank)), e.family, e.display.lower(), e.type))
    return entries
