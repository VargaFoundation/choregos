# SPDX-License-Identifier: Apache-2.0
"""An organisation's sign-in security settings and the revocation of a member's sessions (ADR 0170)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..approval_freshness import MINIMUM_S


class OrgSecurity(BaseModel):
    #: In seconds; `null` removes the requirement. At least 60: below, nobody could ever approve.
    approval_freshness_s: int | None = Field(default=None, ge=MINIMUM_S)


class SessionRevocation(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class SessionsRevoked(BaseModel):
    revoked: str
    ended: int
