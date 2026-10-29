# SPDX-License-Identifier: Apache-2.0
"""Remote agents over A2A (ADR 0062): their credentials, and the discovery of a card."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from choregos_contracts import HEADER_NAME
from pydantic import Field

from .agents import SLUG, AgentCreate
from .base import Dto

RemoteCredentialType = Literal["oauth2_authorization_code", "bearer", "api_key", "none"]


class RemoteCredentialCreate(Dto):
    """An organisation credential for remote agents. No secret is ever read back from the API.

    `oauth2_authorization_code`: give `card_url`; the platform registers itself at the card's Dynamic
    Client Registration endpoint, then an administrator authorises it (`authorize`). `bearer`: give
    the token as `token`, write-only, sealed in the secret store (ADR 0058). `api_key`: the key as
    `token`, sealed the same way, and the request `header` it travels in. `none`: nothing to hold.
    """

    name: str = Field(pattern=SLUG)
    type: RemoteCredentialType
    card_url: str | None = Field(default=None, pattern=r"^https://", max_length=2048)
    token: str | None = Field(default=None, min_length=1, max_length=8192, repr=False)
    #: `api_key`: the request header the key travels in, as the card names it (`x-sn-apikey`).
    header: str | None = Field(default=None, pattern=HEADER_NAME)


class RemoteCredentialDto(Dto):
    name: str
    type: RemoteCredentialType
    #: `api_key`: the request header the key travels in (a name, never the key).
    header: str | None = None
    #: `pending` (registered, nobody authorised yet), `connected`, `error` (the last refresh failed),
    #: `revoked`.
    status: Literal["pending", "connected", "error", "revoked"]
    #: Whose permissions a remote agent acts with: the person who authorised it (OAuth).
    authorised_as: str | None = None
    scopes: list[str] = Field(default_factory=list)
    card_url: str | None = None
    #: The host the credential is registered at (never a URL with a path or a query).
    host: str | None = None
    created_by: str | None = None
    created_at: datetime | None = None
    last_refresh_at: datetime | None = None
    expires_at: datetime | None = None
    last_error: str | None = None


class RemoteCredentialAuthorization(Dto):
    """Where to send the administrator's browser to authorise the credential (OAuth, PKCE)."""

    redirect_url: str


class RemoteAgentDiscover(Dto):
    card_url: str = Field(pattern=r"^https://", max_length=2048)


class RemoteSkillInfo(Dto):
    id: str
    name: str
    description: str = ""


class RemoteCardSummary(Dto):
    name: str
    description: str = ""
    endpoint: str
    host: str
    protocol_version: str
    digest: str
    skills: list[RemoteSkillInfo] = Field(default_factory=list)
    #: `oauth2_authorization_code`, `bearer`, `api_key`, `none`: what the card accepts.
    auth_types: list[str] = Field(default_factory=list)
    #: The request headers the card's API key schemes name.
    api_key_headers: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)
    streaming: bool = False
    #: The card advertises Dynamic Client Registration: an OAuth credential can be created from it.
    dcr_available: bool = False


class RemoteAgentDiscovery(Dto):
    """What a card says, and the registry entry it suggests (never created by this call)."""

    card: RemoteCardSummary
    proposed: AgentCreate
    #: The organisation's credentials of a type the card accepts, to pick from.
    credentials: list[str] = Field(default_factory=list)
    #: Domains the project policy must allow for the runner to reach the agent (egress).
    domains: list[str] = Field(default_factory=list)
