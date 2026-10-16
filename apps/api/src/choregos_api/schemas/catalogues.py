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
