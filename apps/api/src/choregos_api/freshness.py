# SPDX-License-Identifier: Apache-2.0
"""A decision demands a RECENT authentication (ADR 0030, ADR 0035).

Approving a write to the directory, a workstation order, the cut-off of a badge: the session must
be fresh, otherwise a workstation left open would approve in place of its owner. The 401 carries
`step_up_required` and the re-authentication path: the console sends the user there, then back.
It is the same format as the decision of an ontology action, which the console already follows.
"""

from __future__ import annotations

import time
from typing import Any

from .errors import ApiError

REAUTH = "GET /api/v1/auth/login?reauth=1"


def authentication_age(principal: Any, now: float | None = None) -> int | None:
    """In seconds; `None` for an API token or a session without an authentication date."""
    authenticated_at = getattr(principal, "authenticated_at", None)
    if authenticated_at is None:
        return None
    return int(now if now is not None else time.time()) - int(authenticated_at)


def require_fresh_authentication(principal: Any, minutes: int, now: float | None = None) -> int:
    """Returns the age of the authentication; 401 `step_up_required` beyond `minutes`."""
    age = authentication_age(principal, now)
    if age is None or age > minutes * 60:
        since = "an unknown time" if age is None else f"{age // 60} min"
        raise ApiError(
            401,
            "Authentication too old",
            f"this decision needs an authentication less than {minutes} min old; yours is {since} old",
            errors=[{"error": "step_up_required", "reauth": REAUTH}],
        )
    return age
