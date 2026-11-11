# SPDX-License-Identifier: Apache-2.0
"""Connected accounts (ADR 0135 point 6): what `/me/connected-accounts` says. Never a token."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from .base import Dto


class ConnectedAccountDto(Dto):
    org: str
    connector: str
    mode: Literal["user_consent"]
    scopes: list[str]
    #: The person's subject at the source, when its token response named one.
    subject: str | None = None
    #: `connected`, or `reconnect` once the source stopped honouring the consent.
    status: Literal["connected", "reconnect"]
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    created_at: datetime


class ConnectedAccountList(Dto):
    items: list[ConnectedAccountDto]


class ConnectedAccountConnect(Dto):
    """Where the person consents: the source's authorization URL (PKCE S256, signed state)."""

    authorization_url: str
