# SPDX-License-Identifier: Apache-2.0
"""The shipped catalogues as the API shows them (ADR 0065)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import Field

from .base import Dto


class CatalogueMetaDto(Dto):
    """Which file, built when, from what: shared by every catalogue."""

    catalogue_version: str
    generated_at: datetime
    sources: dict[str, str] = Field(default_factory=dict)


class McpCatalogueRemoteDto(Dto):
    url: str
    protocol: Literal["streamable-http", "sse"]


Availability = Literal["all_projects", "selected_projects"]


class CataloguePermissions(Dto):
    """What the caller may do with an entry, decided by the server (ADR 0074)."""

    #: declare it in the organisation
    install: bool
    #: project-scope types: the projects whose settings the caller may set
    configure_projects: list[str] = Field(default_factory=list)
    #: the projects where the caller may turn an installed organisation connector on or off
    enable_projects: list[str] = Field(default_factory=list)


class ConnectorEnablement(Dto):
    """One installed organisation connector and where it is on, among the projects the caller sees."""

    connector: str
    availability: Availability
    enabled_projects: list[str] = Field(default_factory=list)


class McpCatalogueEntryDto(Dto):
    id: str
    title: str
    description: str
    publisher: str
    official: bool
    reference: bool
    origin: Literal["catalogue", "site"] = "catalogue"
    registry_name: str | None = None
    registry_version: str | None = None
    repo: str | None = None
    licence: str | None = None
    terms_url: str | None = None
    transport: Literal["remote", "stdio"]
    remote: McpCatalogueRemoteDto | None = None
    auth: str
    egress_hosts: list[str] = Field(default_factory=list)
    categories: list[str]
    stars: int | None = None
    stars_as_of: date | None = None
    install: Literal["installable", "needs_oauth", "needs_hosting"]
    hosting: dict[str, Any] | None = None
    state: Literal["available", "installed", "update"]
    installed_as: list[str] = Field(default_factory=list)
    egress: Literal["open", "undeclared", "unknown"]
    can: CataloguePermissions
    cannot_reason: str | None = None
    enablement: list[ConnectorEnablement] = Field(default_factory=list)
    #: The setup procedure (`GET /procedures/{id}`) its install shows: its own, else the generic MCP one.
    procedure: str | None = None


class McpCatalogueDto(Dto):
    meta: CatalogueMetaDto
    entries: list[McpCatalogueEntryDto]


class McpCatalogueInstall(Dto):
    name: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{1,62}$")
    secret_refs: dict[str, str] = Field(default_factory=dict)
    #: The credential typed once: sealed at rest, never returned (ADR 0058).
    secret_values: dict[str, str] = Field(default_factory=dict, repr=False)
    auth_header: str | None = Field(default=None, pattern=r"^[A-Za-z0-9-]{1,64}$")
    #: `all_projects` (the API default) or `selected_projects`: only the projects it is enabled for
    availability: Availability = "all_projects"
    #: Open the server's host in the organisation's egress allowlist in the same call (S39-22);
    #: without it a missing host is a 409 `egress_required`. Needs `tools:grant`.
    allow_egress: bool = False


class SkillCatalogueInstall(Dto):
    #: Open the repository's hosts in the organisation's egress allowlist in the same call (S39-22),
    #: for an entry delivered from git; without it a missing host is a 409 `egress_required`.
    allow_egress: bool = False


class SkillCatalogueSignalsDto(Dto):
    stars: int | None = None
    stars_as_of: date | None = None


class SkillCatalogueEntryDto(Dto):
    id: str
    title: str
    description: str
    publisher: str
    repo: str
    sha: str
    path: str
    licence: str
    licence_class: Literal["permissive", "copyleft"]
    files: int
    bytes: int
    digest: str
    risk: Literal["low", "medium", "high"]
    findings_count: int
    categories: list[str]
    signals: SkillCatalogueSignalsDto
    delivery: Literal["bundled", "git"]
    state: Literal["available", "installed", "update", "pending_review", "blocked"]
    installed_version: int | None = None
    blocked_reason: str | None = None
    egress: Literal["open", "undeclared", "unknown"]


class SkillCatalogueDto(Dto):
    meta: CatalogueMetaDto
    entries: list[SkillCatalogueEntryDto]


class ConnectorCatalogueEntryDto(Dto):
    """One connector type of the registry, and what it means in this organisation (ADR 0074)."""

    kind: str
    type: str
    display: str
    family: str
    scope: Literal["organisation", "project"]
    capabilities: list[str]
    secret_fields: list[str]
    #: the secret fields that take several keys, one per slot (ADR 0089)
    pooled_fields: list[str] = Field(default_factory=list)
    #: operations the type declares; an MCP server discovers its own (0)
    operations: int
    installed_as: list[str] = Field(default_factory=list)
    configured_in: list[str] = Field(default_factory=list)
    enablement: list[ConnectorEnablement] = Field(default_factory=list)
    can: CataloguePermissions
    cannot_reason: str | None = None


class ConnectorCatalogueDto(Dto):
    entries: list[ConnectorCatalogueEntryDto]
