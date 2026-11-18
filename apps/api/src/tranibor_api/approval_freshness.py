# SPDX-License-Identifier: Apache-2.0
"""An organisation may demand a RECENT authentication to approve (ADR 0170).

The setting lives in the organisation's `settings` (`approval_freshness_s`, seconds, at least 60;
absent means no requirement). It is read by the gesture check the core plays itself, ahead of any
extension's, for the gestures that APPROVE: a human decision request, a release, an agent release to
production. Answering a question is not approving and is not concerned. The refusal is a 401
`reauth`: the console sends the person to `GET /api/v1/auth/login?reauth=1`, then back.
"""

from __future__ import annotations

import time
from typing import Any

from .db.models import Organization

SETTING = "approval_freshness_s"
MINIMUM_S = 60


def is_approval(gesture: str, target: dict[str, Any]) -> bool:
    if gesture == "release.approve":
        return True
    if gesture == "agent.release":
        # Putting a revision in production (promotion, rollback, first revision) or approving that;
        # sending to test or withdrawing the candidate does not engage production.
        return target.get("decision") == "approve" or (
            target.get("channel") == "stable" and target.get("kind") in {"promote", "rollback", "publish"}
        )
    return target.get("decision") in {"approve", "scope_change"}


def required_seconds(organisation: Organization) -> int | None:
    value = (organisation.settings or {}).get(SETTING)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= MINIMUM_S else None


def stale_reason(organisation: Organization, authenticated_at: int | None, slug: str) -> str | None:
    """The refusal's sentence, or `None` when the authentication is recent enough (or nothing is asked)."""
    limit = required_seconds(organisation)
    if limit is None:
        return None
    age = None if authenticated_at is None else time.time() - authenticated_at
    if age is not None and age <= limit:
        return None
    since = "an unknown time" if age is None else f"{int(age // 60)} min"
    return (
        f"{slug} requires an authentication less than {limit // 60} min old to approve; yours is {since} old"
    )
