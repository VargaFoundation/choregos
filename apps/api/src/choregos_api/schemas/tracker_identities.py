# SPDX-License-Identifier: Apache-2.0
"""A member's account on a tracker, by its stable numeric id (S47-14, ADR 0102)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import Dto


class TrackerIdentityDto(Dto):
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
