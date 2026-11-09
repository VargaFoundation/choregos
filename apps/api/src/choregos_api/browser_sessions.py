# SPDX-License-Identifier: Apache-2.0
"""Browser sessions, recorded so that their owner can see them and end them (ADR 0130).

The signed cookie stays the proof: its signature and its `exp` are checked first, as before. It now
carries `sid`, the id of a `user_sessions` row, and the row says whether the session still holds. A
revoked or unknown row refuses the cookie at its next request, on every replica, with no cache.

A cookie without `sid` was opened before sessions were recorded. It holds until its `exp` (eight
hours at most), unless its owner signed out their other sessions after it was issued
(`users.sessions_not_before`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from choregos_core import utcnow
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection

from .config import Settings
from .db.base import uuid7
from .db.models import User, UserSession
from .errors import unauthorized
from .rate_limit import client_address

#: How stale `last_seen_at` may get before a request rewrites it: one write a minute per session,
#: not one per request.
TOUCH_EVERY = timedelta(seconds=60)
USER_AGENT_CHARS = 300

ENDED = "this session was ended: sign in again"


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _client(request: HTTPConnection, settings: Settings) -> tuple[str | None, str | None]:
    agent = request.headers.get("user-agent", "").strip()[:USER_AGENT_CHARS] or None
    address = client_address(request, settings)
    return agent, (None if address == "unknown" else address[:64])


async def open_session(
    session: AsyncSession,
    user: User,
    *,
    channel: str,
    expires_at: datetime,
    request: HTTPConnection | None = None,
    settings: Settings | None = None,
) -> UserSession:
    """The row a new cookie names, with the browser and address of the sign-in when they are known."""
    row = UserSession(id=uuid7(), user_id=user.id, channel=channel[:32], expires_at=expires_at)
    if request is not None and settings is not None:
        row.user_agent, row.ip = _client(request, settings)
        row.last_seen_at = utcnow()
    session.add(row)
    await session.flush()
    return row


async def check_session(
    session: AsyncSession, user: User, payload: dict[str, Any], request: HTTPConnection, settings: Settings
) -> str | None:
    """Refuse a cookie whose session was ended; return the session's id, or `None` for an older cookie."""
    sid = payload.get("sid")
    now = utcnow()
    if not isinstance(sid, str):
        cutoff = user.sessions_not_before
        iat = payload.get("iat")
        if cutoff is not None and (not isinstance(iat, int | float) or iat < _aware(cutoff).timestamp()):
            raise unauthorized(ENDED)
        return None
    row = await session.get(UserSession, sid)
    if row is None or row.user_id != user.id or row.revoked_at is not None or _aware(row.expires_at) <= now:
        raise unauthorized(ENDED)
    agent, address = _client(request, settings)
    stale = row.last_seen_at is None or now - _aware(row.last_seen_at) >= TOUCH_EVERY
    if stale or agent != row.user_agent or address != row.ip:
        row.last_seen_at = now
        row.user_agent = agent
        row.ip = address
    return sid


async def active_sessions(session: AsyncSession, user_id: str) -> list[UserSession]:
    """The caller's sessions that still hold, the most recently active first."""
    rows = (
        (
            await session.execute(
                select(UserSession).where(
                    UserSession.user_id == user_id,
                    UserSession.revoked_at.is_(None),
                    UserSession.expires_at > utcnow(),
                )
            )
        )
        .scalars()
        .all()
    )
    return sorted(rows, key=lambda r: _aware(r.last_seen_at or r.created_at), reverse=True)


async def end_session(session: AsyncSession, user_id: str, sid: str) -> bool:
    """End one of the caller's sessions; `False` when it is not theirs or already over."""
    result = await session.execute(
        update(UserSession)
        .where(UserSession.id == sid, UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    return bool(getattr(result, "rowcount", 0))


async def end_other_sessions(session: AsyncSession, user: User, keep: str | None) -> int:
    """End every session of the caller but `keep`, and every cookie opened before sessions were recorded.

    Returns how many recorded sessions were ended.
    """
    query = update(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
    if keep is not None:
        query = query.where(UserSession.id != keep)
    result = await session.execute(query.values(revoked_at=utcnow()))
    # `iat` is in whole seconds: truncated, the cutoff refuses every cookie issued before this second.
    user.sessions_not_before = utcnow().replace(microsecond=0)
    return int(getattr(result, "rowcount", 0) or 0)
