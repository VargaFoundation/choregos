# SPDX-License-Identifier: Apache-2.0
"""What bounds the door: the rate per token, the writes per day, the size of the results."""

from __future__ import annotations

from datetime import timedelta

from choregos_core import utcnow
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AuditLog
from ..rate_limit import RateLimiter

#: Per token and per replica: a client in a loop stops, a person in a hurry does not feel it.
CALLS_PER_MINUTE = 120
#: Per person and per day: an agent loop that would create work items stops quickly.
WRITES_PER_DAY = 50
#: Under the 25,000 tokens Claude Code accepts per tool result.
MAX_SIZE = 60_000

RATE_LIMITER = RateLimiter(CALLS_PER_MINUTE)


def truncate(text: str, size: int = MAX_SIZE) -> str:
    if len(text) <= size:
        return text
    return text[:size] + f"\n\n[result_truncated: {len(text) - size} characters omitted]"


async def writes_today(session: AsyncSession, user_id: str) -> int:
    """The writes made through the door in the last 24 hours, read from the audit: nothing else to keep."""
    since = utcnow() - timedelta(days=1)
    rows = (
        await session.execute(
            select(AuditLog.payload).where(
                # The A2A door's writes (ADR 0078) count against the same daily budget.
                AuditLog.action.in_(("mcp.call", "a2a.call")),
                AuditLog.actor_id == user_id,
                AuditLog.ts >= since,
            )
        )
    ).scalars()
    keys = ("write", "ecriture")  # lang: fr-ok — the audit key persisted before S45-39 (ADR 0098 §4)
    return sum(1 for payload in rows if any((payload or {}).get(key) for key in keys))


APPELS_PAR_MINUTE = CALLS_PER_MINUTE  # lang: fr-ok alias:internal
ECRITURES_PAR_JOUR = WRITES_PER_DAY  # lang: fr-ok alias:internal
TAILLE_MAX = MAX_SIZE  # lang: fr-ok alias:internal
LIMITEUR = RATE_LIMITER  # lang: fr-ok alias:internal
tronquer = truncate  # lang: fr-ok alias:internal
ecritures_du_jour = writes_today  # lang: fr-ok alias:internal
