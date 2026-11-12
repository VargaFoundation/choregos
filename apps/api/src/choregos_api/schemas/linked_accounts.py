# SPDX-License-Identifier: Apache-2.0
"""A member's accounts on code forges (ADR 0154; S47-14 and ADR 0102 before it). Never a token."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import Dto

ForgeProvider = Literal["github", "gitlab", "forgejo"]


class LinkedAccountDto(Dto):
    id: str
    org: str
    user_id: str
    provider: ForgeProvider
    #: The forge instance: `https://github.com`, `https://gitlab.example`.
    base_url: str
    #: The forge's stable numeric user id: the only thing ever matched.
    external_id: str
    #: Shown, never matched.
    login: str
    avatar_url: str | None = None
    profile_url: str | None = None
    #: The member proved it by signing in to the forge; `false`: an administrator declared it.
    verified: bool
    verified_at: datetime | None = None
    #: The organisation connector whose OAuth application proved it.
    connector: str | None = None
    created_by: str | None = None
    created_at: datetime


class LinkedAccountDeclare(Dto):
    """An administrator declares a member's account: stored and shown as NOT verified."""

    provider: ForgeProvider
    #: The instance; by default github.com or gitlab.com. Forgejo has no default.
    base_url: str | None = Field(default=None, max_length=255)
    #: The forge's numeric user id, never a login.
    external_id: str = Field(pattern=r"^[0-9]{1,20}$")
    #: Display only: it is never matched.
    login: str = Field(min_length=1, max_length=255)


class ForgeInstanceDto(Dto):
    """An instance a member can link an account on: an organisation connector of kind `forge_account`."""

    org: str
    #: The connector's name: what `instance` names when the link starts.
    instance: str
    provider: ForgeProvider
    base_url: str


class MyLinkedAccounts(Dto):
    items: list[LinkedAccountDto]
    #: Where the caller can link an account, in each of their organisations.
    instances: list[ForgeInstanceDto]


class LinkedAccountStart(Dto):
    """Where the member's browser goes: the forge's authorization URL (PKCE S256, signed state)."""

    authorization_url: str


class TrackerIdentityDto(Dto):
    """Deprecated (ADR 0154): a GitHub link at github.com, as the `tracker-identities` routes answer it."""

    tracker: Literal["github"]
    external_id: str
    login: str
    user_id: str
    created_by: str | None = None
    created_at: datetime


class TrackerIdentityPut(Dto):
    #: The provider's numeric user id, never a login.
    external_id: str = Field(pattern=r"^[0-9]{1,20}$")
    #: Display only: it is never matched.
    login: str = Field(min_length=1, max_length=255)
