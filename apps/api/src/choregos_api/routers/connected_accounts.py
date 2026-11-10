# SPDX-License-Identifier: Apache-2.0
"""The caller's connected accounts (S63-08, ADR 0135 point 6): connect, callback, list, disconnect.

A person owns their accounts: every route reads and writes the caller's own rows only (and PostgreSQL's
restrictive policy would hide another person's even if a route forgot it). No route returns a token.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Cookie, Path, Query, Response, status
from fastapi.responses import RedirectResponse

from ..config import get_settings
from ..db.models import ConnectedAccount
from ..deps import Db, Me
from ..schemas.connected_accounts import ConnectedAccountConnect, ConnectedAccountDto, ConnectedAccountList
from ..services import connected_accounts as service

router = APIRouter(tags=["connectors"])

Connector = Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]{1,62}$")]
CONSENT_PATH = "/api/v1/me/connected-accounts"


def _dto(org: str, account: ConnectedAccount) -> ConnectedAccountDto:
    return ConnectedAccountDto(
        org=org,
        connector=account.connector,
        mode="user_consent",
        scopes=list(account.scopes or []),
        subject=account.subject,
        status="reconnect" if account.status == service.RECONNECT else "connected",
        expires_at=account.expires_at,
        last_used_at=account.last_used_at,
        created_at=account.created_at,
    )


@router.get(
    "/me/connected-accounts", response_model=ConnectedAccountList, operation_id="listMyConnectedAccounts"
)
async def list_my_connected_accounts(session: Db, principal: Me) -> ConnectedAccountList:
    return ConnectedAccountList(
        items=[_dto(org, account) for org, account in await service.accounts_of(session, principal)]
    )


@router.post(
    "/me/connected-accounts/{connector}/connect",
    response_model=ConnectedAccountConnect,
    operation_id="connectMyAccount",
)
async def connect_my_account(
    connector: Connector,
    org: Annotated[str, Query(min_length=1, max_length=63)],
    response: Response,
    session: Db,
    principal: Me,
) -> ConnectedAccountConnect:
    url, cookie = await service.begin(session, principal, org, connector)
    settings = get_settings()
    response.set_cookie(
        service.HANDSHAKE_COOKIE,
        cookie,
        max_age=service.HANDSHAKE_S,
        path=CONSENT_PATH,
        httponly=True,
        samesite="lax",
        secure=settings.cookies_secure,
    )
    return ConnectedAccountConnect(authorization_url=url)


@router.get(
    "/me/connected-accounts/callback",
    status_code=status.HTTP_303_SEE_OTHER,
    response_class=RedirectResponse,
    operation_id="completeMyAccountConnection",
)
async def complete_my_account_connection(
    code: Annotated[str, Query(min_length=1, max_length=2000)],
    state: Annotated[str, Query(min_length=1, max_length=4000)],
    session: Db,
    principal: Me,
    handshake: Annotated[str | None, Cookie(alias=service.HANDSHAKE_COOKIE)] = None,
) -> RedirectResponse:
    account = await service.complete(session, principal, code=code, state=state, cookie=handshake or "")
    base = get_settings().public_url.rstrip("/")
    redirect = RedirectResponse(
        f"{base}/me/connected-accounts?{urlencode({'connected': account.connector})}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    redirect.delete_cookie(service.HANDSHAKE_COOKIE, path=CONSENT_PATH)
    return redirect


@router.delete(
    "/me/connected-accounts/{connector}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="disconnectMyAccount",
)
async def disconnect_my_account(
    connector: Connector, org: Annotated[str, Query(min_length=1, max_length=63)], session: Db, principal: Me
) -> Response:
    await service.disconnect(session, principal, org, connector)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
