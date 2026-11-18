# SPDX-License-Identifier: Apache-2.0
"""Standing delegations (ADR 0137 points 3-4, S63-14): the DTOs of `/me/delegations`."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import Dto


class DelegationCreate(Dto):
    """What a person agrees to: the task, and the exact needs it reads (`agree_to` must equal the list the
    409 shows). `expires_in_days` defaults to 90 and is capped by the installation's maximum."""

    org: str = Field(min_length=1, max_length=64)
    assistant: str = Field(min_length=1, max_length=63)
    task: str = Field(min_length=1, max_length=63)
    agree_to: list[str] = Field(default_factory=list, max_length=64)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class DelegationNeed(Dto):
    need: str
    operations: list[str]
    identity: Literal["service", "user_exchange", "user_consent"]


class Delegation(Dto):
    id: str
    org: str
    assistant: str
    task: str
    needs: list[DelegationNeed]
    granted_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
    #: `active`, `suspended` (with `suspended_reason`), `revoked`, or `expired` (the clock, never stored).
    status: Literal["active", "suspended", "revoked", "expired"]
    suspended_reason: str | None = None
    manifest_digest: str
    #: When the person was told it ends soon (the reminder, once), if they were.
    reminded_at: datetime | None = None


class DelegationList(Dto):
    items: list[Delegation]
