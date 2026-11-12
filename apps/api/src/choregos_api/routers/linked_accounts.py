# SPDX-License-Identifier: Apache-2.0
"""A member's accounts on code forges (S66-03, ADR 0154): linked by the member and proven, or declared.

- The member: `GET /me/linked-accounts` (their links and where they can add one), `POST
  /me/linked-accounts/{provider}/start` (the forge's authorization URL), the forge's redirect to
  `GET /auth/linked-accounts/callback`, and `DELETE /me/linked-accounts/{id}`.
- An organisation administrator: a member's links, a declared link (NOT verified), its removal. The member
  reads their own links on the same route.
- Deprecated (ADR 0154): the `tracker-identities` routes of S47-14, a GitHub link at github.com.

No route returns a token: the forge's access token is used once, to read the user, and dropped.
"""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import urlencode

from choregos_adapters.forge_accounts import GITHUB_COM
from fastapi import APIRouter, Cookie, Path, Query, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from ..config import get_settings
from ..db.models import LinkedAccount, Organization
from ..deps import Db, Me
from ..errors import forbidden, not_found
from ..rbac import Permission, Principal
from ..schemas import (
    ForgeInstanceDto,
    LinkedAccountDeclare,
    LinkedAccountDto,
    LinkedAccountStart,
    MyLinkedAccounts,
    TrackerIdentityDto,
    TrackerIdentityPut,
)
from ..services import linked_accounts as service
from ..services.organisations import organisation_by_slug

router = APIRouter(tags=["admin"])
me_router = APIRouter(tags=["session"])

Provider = Literal["github", "gitlab", "forgejo"]
Tracker = Literal["github"]
AccountId = Annotated[str, Path(min_length=1, max_length=36)]
#: Where the console shows a member's links, and the callback sends the browser back.
CONSOLE_PAGE = "/account"


def _dto(org: str, row: LinkedAccount) -> LinkedAccountDto:
    return LinkedAccountDto.model_validate({**_columns(row), "org": org})


def _columns(row: LinkedAccount) -> dict[str, object]:
    return {
        name: getattr(row, name)
        for name in (
            "id",
            "user_id",
            "provider",
            "base_url",
            "external_id",
            "login",
            "avatar_url",
            "profile_url",
            "verified",
            "verified_at",
            "connector",
            "created_by",
            "created_at",
        )
    }


async def _organisation_member(
    session: Db, principal: Principal, org: str, user_id: str, *, read: bool = False
) -> Organization:
    """The organisation, once the caller may act on the member's links (an administrator; the member
    themself to read) and the user is a member of the organisation itself."""
    own = read and principal.user_id == user_id and org in principal.org_roles
    if not (own or principal.can(Permission.MEMBER_MANAGE, org)):
        raise forbidden("a member's linked accounts are managed by an org_admin")
    organisation = await organisation_by_slug(session, org)
    if not await service.is_member(session, organisation.id, user_id):
        raise not_found("Member", user_id)
    return organisation


async def _rows_of(session: Db, org_id: str, user_id: str) -> list[LinkedAccount]:
    return list(
        (
            await session.execute(
                select(LinkedAccount)
                .where(LinkedAccount.org_id == org_id, LinkedAccount.user_id == user_id)
                .order_by(LinkedAccount.provider, LinkedAccount.base_url)
            )
        )
        .scalars()
        .all()
    )


@router.get(
    "/orgs/{org}/members/{user_id}/linked-accounts",
    response_model=list[LinkedAccountDto],
    operation_id="listMemberLinkedAccounts",
)
async def list_member_linked_accounts(
    org: str, user_id: str, session: Db, principal: Me
) -> list[LinkedAccountDto]:
    organisation = await _organisation_member(session, principal, org, user_id, read=True)
    return [_dto(organisation.slug, row) for row in await _rows_of(session, organisation.id, user_id)]


@router.post(
    "/orgs/{org}/members/{user_id}/linked-accounts",
    response_model=LinkedAccountDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="declareMemberLinkedAccount",
)
async def declare_member_linked_account(
    org: str, user_id: str, body: LinkedAccountDeclare, session: Db, principal: Me
) -> LinkedAccountDto:
    """Declares the member's account on an instance, replacing their link there: NOT verified."""
    organisation = await _organisation_member(session, principal, org, user_id)
    row = await service.declare(
        session,
        principal,
        organisation.id,
        user_id,
        service.Declared(
            body.provider, service.base_url_for(body.provider, body.base_url), body.external_id, body.login
        ),
    )
    return _dto(organisation.slug, row)


@router.delete(
    "/orgs/{org}/members/{user_id}/linked-accounts/{account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteMemberLinkedAccount",
)
async def delete_member_linked_account(
    org: str, user_id: str, account_id: AccountId, session: Db, principal: Me
) -> Response:
    organisation = await _organisation_member(session, principal, org, user_id)
    row = await session.get(LinkedAccount, account_id)
    if row is None or row.org_id != organisation.id or row.user_id != user_id:
        raise not_found("Linked account", account_id)
    await service.remove(session, principal, row)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@me_router.get("/me/linked-accounts", response_model=MyLinkedAccounts, operation_id="listMyLinkedAccounts")
async def list_my_linked_accounts(session: Db, principal: Me) -> MyLinkedAccounts:
    rows = (
        await session.execute(
            select(LinkedAccount, Organization.slug)
            .join(Organization, Organization.id == LinkedAccount.org_id)
            .where(LinkedAccount.user_id == principal.user_id)
            .order_by(Organization.slug, LinkedAccount.provider, LinkedAccount.base_url)
        )
    ).all()
    instances = await service.instances_for(session, principal)
    return MyLinkedAccounts(
        items=[_dto(slug, row) for row, slug in rows],
        instances=[
            ForgeInstanceDto(
                org=i.org.slug,
                instance=i.connector.name,
                provider=i.provider,
                base_url=i.base_url,
            )
            for i in instances
        ],
    )


@me_router.post(
    "/me/linked-accounts/{provider}/start",
    response_model=LinkedAccountStart,
    operation_id="startMyLinkedAccount",
)
async def start_my_linked_account(
    provider: Provider,
    org: Annotated[str, Query(min_length=1, max_length=63)],
    instance: Annotated[str, Query(pattern=r"^[a-z][a-z0-9-]{1,62}$")],
    response: Response,
    session: Db,
    principal: Me,
) -> LinkedAccountStart:
    """The forge's authorization URL; the PKCE verifier waits in a cookie only the callback receives."""
    url, cookie = await service.begin(session, principal, org, provider, instance)
    response.set_cookie(
        service.HANDSHAKE_COOKIE,
        cookie,
        max_age=service.HANDSHAKE_S,
        path=service.COOKIE_PATH,
        httponly=True,
        samesite="lax",
        secure=get_settings().cookies_secure,
    )
    return LinkedAccountStart(authorization_url=url)


@me_router.get(
    "/auth/linked-accounts/callback",
    status_code=status.HTTP_303_SEE_OTHER,
    response_class=RedirectResponse,
    operation_id="completeMyLinkedAccount",
)
async def complete_my_linked_account(
    session: Db,
    principal: Me,
    state: Annotated[str, Query(min_length=1, max_length=4000)],
    code: Annotated[str | None, Query(max_length=2000)] = None,
    error: Annotated[str | None, Query(max_length=200)] = None,
    handshake: Annotated[str | None, Cookie(alias=service.HANDSHAKE_COOKIE)] = None,
) -> RedirectResponse:
    """Back from the forge: the verified link, then My account (`?linked=` or `?link_error=`)."""
    base = get_settings().public_url.rstrip("/")
    try:
        if error or not code:
            raise service.LinkRefused("denied", "the forge did not authorise the link")
        account = await service.complete(session, principal, code=code, state=state, cookie=handshake or "")
        query = {"linked": account.provider}
    except service.LinkRefused as refused:
        query = {"link_error": refused.code}
    redirect = RedirectResponse(f"{base}{CONSOLE_PAGE}?{urlencode(query)}#linked-accounts", status_code=303)
    redirect.delete_cookie(service.HANDSHAKE_COOKIE, path=service.COOKIE_PATH)
    return redirect


@me_router.delete(
    "/me/linked-accounts/{account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteMyLinkedAccount",
)
async def delete_my_linked_account(account_id: AccountId, session: Db, principal: Me) -> Response:
    row = await session.get(LinkedAccount, account_id)
    if row is None or row.user_id != principal.user_id:
        raise not_found("Linked account", account_id)
    await service.remove(session, principal, row)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Deprecated (ADR 0154): the S47-14 routes, a GitHub link at github.com. Kept for the callers written
# against them; the console uses the routes above.


async def _github_link(session: Db, org_id: str, user_id: str) -> LinkedAccount | None:
    return next(
        (
            row
            for row in await _rows_of(session, org_id, user_id)
            if row.provider == "github" and row.base_url == GITHUB_COM
        ),
        None,
    )


def _tracker_dto(row: LinkedAccount) -> TrackerIdentityDto:
    return TrackerIdentityDto(
        tracker="github",
        external_id=row.external_id,
        login=row.login,
        user_id=row.user_id,
        created_by=row.created_by,
        created_at=row.created_at,
    )


@router.get(
    "/orgs/{org}/members/{user_id}/tracker-identities",
    response_model=list[TrackerIdentityDto],
    operation_id="listTrackerIdentities",
    deprecated=True,
)
async def list_tracker_identities(
    org: str, user_id: str, session: Db, principal: Me
) -> list[TrackerIdentityDto]:
    organisation = await _organisation_member(session, principal, org, user_id)
    row = await _github_link(session, organisation.id, user_id)
    return [_tracker_dto(row)] if row is not None else []


@router.put(
    "/orgs/{org}/members/{user_id}/tracker-identities/{tracker}",
    response_model=TrackerIdentityDto,
    operation_id="setTrackerIdentity",
    deprecated=True,
)
async def set_tracker_identity(
    org: str, user_id: str, tracker: Tracker, body: TrackerIdentityPut, session: Db, principal: Me
) -> TrackerIdentityDto:
    organisation = await _organisation_member(session, principal, org, user_id)
    row = await service.declare(
        session,
        principal,
        organisation.id,
        user_id,
        service.Declared(tracker, GITHUB_COM, body.external_id, body.login),
        action="tracker_identity.set",
    )
    return _tracker_dto(row)


@router.delete(
    "/orgs/{org}/members/{user_id}/tracker-identities/{tracker}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteTrackerIdentity",
    deprecated=True,
)
async def delete_tracker_identity(
    org: str, user_id: str, tracker: Tracker, session: Db, principal: Me
) -> Response:
    organisation = await _organisation_member(session, principal, org, user_id)
    row = await _github_link(session, organisation.id, user_id)
    if row is None:
        raise not_found("Tracker identity", tracker)
    await service.remove(session, principal, row, action="tracker_identity.delete")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
