# SPDX-License-Identifier: Apache-2.0
"""Who calls: members, organisations, API tokens."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from choregos_contracts import Role
from choregos_core.agent_completeness import OrgCompleteAgents
from choregos_core.tool_scripts_switch import OrgToolScripts
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


#: A token's scopes (ADR 0030). `*` opens the REST API and the CLI; `mcp:read` and `mcp:write`
#: open only the MCP door, and a token that carries them is refused by the REST API.
#: `a2a` opens only the A2A door (ADR 0078).
TokenScope = Literal["*", "mcp:read", "mcp:write", "a2a"]


def _whole_api() -> list[TokenScope]:
    return ["*"]


class ApiTokenCreate(Dto):
    name: str = Field(min_length=1, max_length=128)
    #: No expiry when absent, but saying "never" is a choice, not an oversight.
    expires_in_days: int | None = Field(default=90, ge=1, le=3650)
    scopes: list[TokenScope] = Field(default_factory=_whole_api, min_length=1)
    #: `org:slug`: bounds an MCP token to a single project.
    project: str | None = None


class ApiTokenDto(Dto):
    id: str
    name: str
    created_at: datetime
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    scopes: list[TokenScope] = Field(default_factory=_whole_api)
    project: str | None = None
    last_client: str | None = None


class MachineAccountCreate(Dto):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    display_name: str | None = Field(default=None, max_length=200)
    writes_per_day: int = Field(default=500, ge=1, le=10_000)


class MachineTokenCreate(Dto):
    name: str = Field(min_length=1, max_length=128)
    expires_in_days: int = Field(default=90, ge=1, le=365)


class MachineAccountDto(Dto):
    slug: str
    display_name: str
    address: str
    writes_per_day: int
    created_by: str | None = None
    created_at: datetime
    tokens: list[ApiTokenDto] = Field(default_factory=list)


class BrowserSessionDto(Dto):
    """One of the caller's browser sessions that still holds (ADR 0130)."""

    id: str
    #: How it was opened: `oidc`, `saml`, `dev`...
    channel: str
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime | None = None
    #: The `User-Agent` and the client address of its last request.
    user_agent: str | None = None
    ip: str | None = None
    #: The session this request came with.
    current: bool = False


class EndedSessionsDto(Dto):
    #: How many recorded sessions were ended.
    ended: int


class ApiTokenCreated(ApiTokenDto):
    #: The token in clear, returned ONCE. It is never stored.
    token: str


class MembershipUpsert(Dto):
    email: str
    role: Role
    project_slug: str | None = None


class OrgDto(Dto):
    slug: str
    name: str
    role: Role | None = None
    #: The currency the console shows spend in (S29-03, decision D4): an organisation setting, EUR by
    #: default. The ledger holds both USD and EUR.
    currency: Literal["EUR", "USD"] = "EUR"
    #: The slugs the organisation had: they still resolve to it (ADR 0090).
    aliases: list[str] = Field(default_factory=list)
    #: Whether the organisation's runs may be offered tool scripts (S57-05b, ADR 0122 §6): `enabled`,
    #: `disabled`, or `None` to follow the deployment's default. Its administrator changes it.
    tool_scripts: OrgToolScripts | None = None
    #: Whether a new version of an internal agent must inherit nothing from its project (S60-05,
    #: ADR 0127 §8): `required`, or `None`. Its administrator changes it.
    complete_agents: OrgCompleteAgents | None = None


class OrgCreate(Dto):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    name: str = Field(min_length=1, max_length=200)


class OrgUpdate(Dto):
    """What changes on an organisation: its name, its slug, its currency, its tool scripts."""

    slug: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    currency: Literal["EUR", "USD"] | None = None
    #: Absent: unchanged. `enabled` or `disabled`: the organisation's choice. `null`: back to the
    #: deployment's default (S57-05b) — unlike the other fields, where `null` changes nothing.
    tool_scripts: OrgToolScripts | None = None
    #: Absent: unchanged. `required`: new versions must be complete (ADR 0127). `null`: no longer required.
    complete_agents: OrgCompleteAgents | None = None


class OAuthClientDto(Dto):
    client_id: str
    callback_port: int | None = None


class OAuthDto(Dto):
    enabled: bool
    authorization_server: str | None = None
    #: The clients pre-registered with the IdP, per client of the Integrations page.
    clients: dict[str, OAuthClientDto] = Field(default_factory=dict)


class IntegrationsDto(Dto):
    """Where to connect an MCP client (ADR 0030)."""

    mcp_url: str
    protocol_versions: list[str]
    oauth: OAuthDto
    version: str
