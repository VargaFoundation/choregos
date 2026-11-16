# SPDX-License-Identifier: Apache-2.0
"""A developer surface signs in as the person: the device flow of RFC 8628, brokered by the console.

`POST /auth/device` gives the surface two codes; the person opens the console's `/device` page, signs in
through their organisation's identity provider as for any sign-in, and approves the user code; the surface,
which polls `POST /auth/device/token`, receives a session of its own. That session is the person's: it carries
the approving sign-in's authentication time, is listed and ended with the browser sessions (ADR 0130), and
ends after a period of inactivity.

What each guard is for:

- only the hashes of the codes are stored, and a device code is single-use: the exchange is one conditional
  update, so two surfaces holding the same code cannot both win;
- polling faster than `interval` answers `slow_down` and lengthens the interval (RFC 8628 §3.5);
- the page that approves needs the person's own console session: an API token, or another surface's
  session, never approves a sign-in;
- a request that names an organisation is approved only by someone that organisation's identity provider
  may have signed in: a member of it, and, when the account is bound to a tenant provider, that same tenant's.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from choregos_core import utcnow
from fastapi import APIRouter, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from ..audit import record
from ..browser_sessions import open_surface_session, surface_credential
from ..db.models import DeviceAuthorization, Membership, Organization, User
from ..db.org_aliases import current_slug
from ..db.session import in_platform_scope
from ..deps import Config, Db, Me
from ..errors import ApiError, conflict, forbidden, not_found, unauthorized
from ..schemas.identity import (
    DeviceAuthorizationDto,
    DeviceAuthorizationRequestDto,
    DeviceDecisionDto,
    DeviceRequestDto,
    DeviceTokenDto,
    DeviceTokenRequestDto,
)

router = APIRouter(tags=["session"])

#: No `0`, `O`, `1`, `I`, `L` and no vowel: a code copied by hand is not misread, and spells nothing.
ALPHABET = "BCDFGHJKMNPQRSTVWXZ23456789"
CODE_LENGTH = 8
#: RFC 8628 §3.5: each `slow_down` adds five seconds to the interval.
SLOW_DOWN_STEP_S = 5
GRANT = "urn:ietf:params:oauth:grant-type:device_code"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def normalise(user_code: str) -> str:
    """`abcd-efgh`, ` ABCD EFGH ` and `ABCDEFGH` are one code."""
    return "".join(ch for ch in user_code.upper() if ch.isalnum())


def _display(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _error(error: str, description: str, **extra: Any) -> JSONResponse:
    """An RFC 8628 §3.5 answer: a 400 whose body is the error, not a problem document."""
    return JSONResponse(
        {"error": error, "error_description": description, **extra},
        status_code=400,
        headers={"Cache-Control": "no-store"},
    )


@router.post("/auth/device", response_model=DeviceAuthorizationDto, operation_id="startDeviceSignIn")
async def start_device_sign_in(
    body: DeviceAuthorizationRequestDto, session: Db, settings: Config
) -> DeviceAuthorizationDto:
    """Two codes: the surface keeps `device_code`; the person is shown `user_code` at `verification_uri`."""
    org_slug: str | None = None
    async with in_platform_scope(session):
        if body.org:
            org_slug = await current_slug(session, body.org)
            if (
                await session.execute(select(Organization.id).where(Organization.slug == org_slug))
            ).scalar_one_or_none() is None:
                raise not_found("Organisation", body.org)
        for _ in range(5):
            device_code = secrets.token_urlsafe(32)
            user_code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
            row = DeviceAuthorization(
                device_hash=_hash(device_code),
                user_code_hash=_hash(user_code),
                client_kind=body.client_kind,
                client_name=(body.client_name or "").strip()[:80] or None,
                org_slug=org_slug,
                status="pending",
                expires_at=utcnow() + timedelta(seconds=settings.device_code_ttl_s),
                interval_s=settings.device_poll_interval_s,
            )
            try:
                async with session.begin_nested():
                    session.add(row)
                    await session.flush()
            except IntegrityError:  # a user code already taken: draw another
                continue
            break
        else:  # pragma: no cover - 27^8 codes, five draws
            raise ApiError(503, "Try again", "no free sign-in code")
    base = settings.public_url.rstrip("/") + "/device"
    return DeviceAuthorizationDto(
        device_code=device_code,
        user_code=_display(user_code),
        verification_uri=base,
        verification_uri_complete=f"{base}?user_code={_display(user_code)}",
        expires_in=settings.device_code_ttl_s,
        interval=settings.device_poll_interval_s,
    )


@router.post(
    "/auth/device/token",
    response_model=DeviceTokenDto,
    operation_id="pollDeviceToken",
    responses={400: {"description": "An RFC 8628 error"}},
)
async def poll_device_token(
    body: DeviceTokenRequestDto, request: Request, session: Db, settings: Config
) -> Response | DeviceTokenDto:
    async with in_platform_scope(session):
        row = (
            await session.execute(
                select(DeviceAuthorization).where(DeviceAuthorization.device_hash == _hash(body.device_code))
            )
        ).scalar_one_or_none()
        if row is None:
            return _error("invalid_grant", "unknown device code")
        now = utcnow()
        if row.status == "consumed" or _aware(row.expires_at) <= now:
            return _error("expired_token", "the device code expired or was already used")
        if (
            row.last_polled_at is not None
            and (now - _aware(row.last_polled_at)).total_seconds() < row.interval_s
        ):
            row.interval_s += SLOW_DOWN_STEP_S
            row.last_polled_at = now
            return _error("slow_down", "poll no faster than `interval`", interval=row.interval_s)
        row.last_polled_at = now
        if row.status == "pending":
            return _error("authorization_pending", "the person has not decided yet")
        if row.status == "denied":
            return _error("access_denied", "the person refused this sign-in")
        # approved: one conditional update decides who consumes the code
        won = await session.execute(
            update(DeviceAuthorization)
            .where(DeviceAuthorization.id == row.id, DeviceAuthorization.status == "approved")
            .values(status="consumed")
        )
        if not getattr(won, "rowcount", 0):
            return _error("expired_token", "the device code expired or was already used")
        user = await session.get(User, row.user_id) if row.user_id else None
        if user is None:
            return _error("access_denied", "the account no longer exists")
        opened = await open_surface_session(
            session,
            user,
            kind=row.client_kind,
            client_name=row.client_name,
            auth_time=row.auth_time,
            request=request,
            settings=settings,
        )
        await record(
            session,
            None,
            "session.surface.open",
            org_id=None,
            target_type="session",
            target_id=opened.id,
            kind=row.client_kind,
            user_id=user.id,
        )
        return DeviceTokenDto(
            access_token=surface_credential(opened, settings),
            session_id=opened.id,
            kind=row.client_kind,
            expires_in=settings.surface_session_idle_s,
            auth_time=row.auth_time,
        )


def _console_only(principal: Any) -> None:
    """The person's own console session decides: nothing that holds a token can approve a sign-in."""
    if principal.credential != "cookie" or principal.kind != "user":
        raise forbidden(
            "a sign-in is approved in the console, with your own session: not with an API token, "
            "an embed token or another developer surface's session"
        )


async def _pending(session: Db, user_code: str) -> DeviceAuthorization:
    row = (
        await session.execute(
            select(DeviceAuthorization).where(
                DeviceAuthorization.user_code_hash == _hash(normalise(user_code))
            )
        )
    ).scalar_one_or_none()
    if row is None or _aware(row.expires_at) <= utcnow() or row.status == "consumed":
        raise not_found("Sign-in code", "this code")
    return row


@router.get(
    "/auth/device/requests/{user_code}", response_model=DeviceRequestDto, operation_id="getDeviceRequest"
)
async def get_device_request(user_code: str, session: Db, principal: Me) -> DeviceRequestDto:
    _console_only(principal)
    async with in_platform_scope(session):
        row = await _pending(session, user_code)
        return DeviceRequestDto(
            user_code=_display(normalise(user_code)),
            client_kind=row.client_kind,
            client_name=row.client_name,
            org=row.org_slug,
            expires_at=row.expires_at,
        )


async def _may_approve(session: Db, user: User, org_slug: str) -> bool:
    """A member of the organisation the request names; and, when the account is bound to a tenant's own
    identity provider (`tenant:<org id>:<sub>`, ADR 0108), the provider of THAT organisation."""
    org = (
        await session.execute(select(Organization).where(Organization.slug == org_slug))
    ).scalar_one_or_none()
    if org is None:
        return False
    member = (
        await session.execute(
            select(Membership.id).where(Membership.user_id == user.id, Membership.org_id == org.id).limit(1)
        )
    ).first()
    if member is None:
        return False
    subject = user.oidc_sub or ""
    return not subject.startswith("tenant:") or subject.split(":", 2)[1] == org.id


@router.post(
    "/auth/device/requests/{user_code}/decision",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="decideDeviceRequest",
)
async def decide_device_request(
    user_code: str, body: DeviceDecisionDto, session: Db, principal: Me
) -> Response:
    _console_only(principal)
    async with in_platform_scope(session):
        row = await _pending(session, user_code)
        if row.status != "pending":
            raise conflict("this sign-in was already decided")
        user = await session.get(User, principal.user_id)
        if user is None:
            raise unauthorized()
        if body.decision == "approve":
            if row.org_slug and not await _may_approve(session, user, row.org_slug):
                raise forbidden(
                    f"this sign-in asks for the identity provider of {row.org_slug}: "
                    "yours is not that organisation's"
                )
            row.status = "approved"
            row.user_id = user.id
            moment = principal.authenticated_at
            row.auth_time = datetime.fromtimestamp(moment, tz=UTC) if moment is not None else None
        else:
            row.status = "denied"
        await record(
            session,
            principal,
            "session.surface.approve" if body.decision == "approve" else "session.surface.deny",
            org_id=None,
            target_type="device_authorization",
            target_id=row.id,
            kind=row.client_kind,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
