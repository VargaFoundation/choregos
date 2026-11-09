# SPDX-License-Identifier: Apache-2.0
"""The caller's browser sessions: list them, end one, end all the others (ADR 0130)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response, status

from ..audit import record
from ..browser_sessions import active_sessions, end_other_sessions, end_session, open_session
from ..db.models import User
from ..deps import Config, Db, Me
from ..errors import not_found, unauthorized
from ..schemas import BrowserSessionDto, EndedSessionsDto
from ..security import read_session, sign_session

router = APIRouter(tags=["session"])


@router.get("/me/sessions", response_model=list[BrowserSessionDto], operation_id="listMySessions")
async def list_my_sessions(session: Db, principal: Me) -> list[BrowserSessionDto]:
    """The caller's sessions that still hold, the most recently active first."""
    return [
        BrowserSessionDto(
            id=row.id,
            channel=row.channel,
            created_at=row.created_at,
            expires_at=row.expires_at,
            last_seen_at=row.last_seen_at,
            user_agent=row.user_agent,
            ip=row.ip,
            current=row.id == principal.session_id,
        )
        for row in await active_sessions(session, principal.user_id)
    ]


@router.delete("/me/sessions/{id}", status_code=status.HTTP_204_NO_CONTENT, operation_id="endMySession")
async def end_my_session(id: str, session: Db, principal: Me, settings: Config) -> Response:
    """End one of the caller's sessions: its cookie is refused from its next request, wherever it is."""
    if not await end_session(session, principal.user_id, id):
        raise not_found("Session", id)
    await record(session, principal, "session.end", org_id=None, target_type="session", target_id=id)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    if id == principal.session_id:
        response.delete_cookie(settings.session_cookie)
    return response


@router.post("/me/sessions/end-others", response_model=EndedSessionsDto, operation_id="endMyOtherSessions")
async def end_my_other_sessions(
    request: Request, response: Response, session: Db, principal: Me, settings: Config
) -> EndedSessionsDto:
    """End every session of the caller but the one this request came with.

    Cookies opened before sessions were recorded end too. When THIS request came with such a cookie,
    the browser receives a recorded session in its place, with the same expiry: it stays signed in.
    """
    user = await session.get(User, principal.user_id)
    if user is None:
        raise unauthorized()
    ended = await end_other_sessions(session, user, principal.session_id)
    payload = read_session(request.cookies.get(settings.session_cookie, ""), settings)
    if principal.session_id is None and payload is not None and payload.get("sub") == user.id:
        expires = int(payload.get("exp", 0))
        recorded = await open_session(
            session,
            user,
            channel="renewed",
            expires_at=datetime.fromtimestamp(expires, tz=UTC),
            request=request,
            settings=settings,
        )
        response.set_cookie(
            settings.session_cookie,
            sign_session({**payload, "sid": recorded.id}, settings),
            max_age=max(0, expires - int(datetime.now(UTC).timestamp())),
            httponly=True,
            samesite="lax",
            secure=settings.cookies_secure,
        )
    await record(
        session,
        principal,
        "session.end_others",
        org_id=None,
        target_type="user",
        target_id=user.id,
        ended=ended,
    )
    return EndedSessionsDto(ended=ended)
