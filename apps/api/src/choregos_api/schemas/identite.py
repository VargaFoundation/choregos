# SPDX-License-Identifier: Apache-2.0
"""Qui appelle : membres, organisations, jetons d'API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from choregos_contracts import Role
from pydantic import Field

from .base import Dto


class MembershipDto(Dto):
    user_id: str | None = None
    email: str | None = None
    org: str
    project_slug: str | None = None
    role: Role


class MeDto(Dto):
    id: str
    email: str
    display_name: str
    oidc_sub: str | None = None
    last_login_at: datetime | None = None
    memberships: list[MembershipDto] = Field(default_factory=list)


#: Les portées d'un jeton (ADR 0030). `*` ouvre l'API REST et la CLI ; `mcp:read` et `mcp:write`
#: n'ouvrent que la porte MCP — et un jeton qui les porte est refusé par l'API REST.
PorteeDeJeton = Literal["*", "mcp:read", "mcp:write"]


def _toute_l_api() -> list[PorteeDeJeton]:
    return ["*"]


class ApiTokenCreate(Dto):
    name: str = Field(min_length=1, max_length=128)
    #: Sans expiration si absent — mais dire « jamais » est un choix, pas un oubli.
    expires_in_days: int | None = Field(default=90, ge=1, le=3650)
    scopes: list[PorteeDeJeton] = Field(default_factory=_toute_l_api, min_length=1)
    #: `org:slug` : borne un jeton MCP à un seul projet.
    project: str | None = None


class ApiTokenDto(Dto):
    id: str
    name: str
    created_at: datetime
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    scopes: list[PorteeDeJeton] = Field(default_factory=_toute_l_api)
    project: str | None = None
    last_client: str | None = None


class ApiTokenCreated(ApiTokenDto):
    #: Le jeton en clair, rendu UNE fois. Il n'est jamais stocké.
    token: str


class MembershipUpsert(Dto):
    email: str
    role: Role
    project_slug: str | None = None


class OrgDto(Dto):
    slug: str
    name: str
    role: Role | None = None


class OrgCreate(Dto):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    name: str = Field(min_length=1, max_length=200)


class OAuthDto(Dto):
    enabled: bool
    authorization_server: str | None = None


class IntegrationsDto(Dto):
    """Où connecter un client MCP (ADR 0030)."""

    mcp_url: str
    protocol_versions: list[str]
    oauth: OAuthDto
    version: str
