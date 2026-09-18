"""Session : OIDC (authlib) en production, connexion de développement en local."""

from __future__ import annotations

import time
from typing import Annotated, Any
from urllib.parse import quote, urlencode

from choregos_contracts import Role
from choregos_core import utcnow
from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from ..audit import record
from ..config import Settings
from ..db.models import Membership, Organization, User
from ..deps import Config, Db, Me
from ..errors import unauthorized
from ..schemas import MeDto, MembershipDto

router = APIRouter(tags=["session"])


@router.get("/me", response_model=MeDto, operation_id="getMe")
async def get_me(session: Db, principal: Me) -> MeDto:
    user = await session.get(User, principal.user_id)
    if user is None:
        raise unauthorized()
    memberships = [
        MembershipDto(org=org, role=role, user_id=user.id, email=user.email)
        for org, role in principal.org_roles.items()
    ]
    memberships += [
        MembershipDto(
            org=next(iter(principal.org_roles), ""),
            project_slug=slug,
            role=role,
            user_id=user.id,
            email=user.email,
        )
        for slug, role in principal.project_roles.items()
    ]
    return MeDto(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        oidc_sub=user.oidc_sub,
        last_login_at=user.last_login_at,
        memberships=memberships,
    )


def _oidc_urls(settings: Settings) -> dict[str, str]:
    base = settings.oidc_issuer.rstrip("/")
    return {
        "authorize": f"{base}/protocol/openid-connect/auth",
        "token": f"{base}/protocol/openid-connect/token",
        "userinfo": f"{base}/protocol/openid-connect/userinfo",
    }


@router.get("/auth/login", operation_id="authLogin")
async def login(
    request: Request,
    settings: Config,
    redirect_to: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    """Redirige vers l'IdP. En dev, `?as=email` ouvre une session directement."""
    as_user = request.query_params.get("as")
    if as_user and settings.dev_login_enabled:
        target = redirect_to or settings.public_url
        return RedirectResponse(
            url=f"/api/v1/auth/callback?code=dev:{as_user}&state={quote(target, safe='')}",
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )
    params = {
        "client_id": settings.oidc_client_id,
        "response_type": "code",
        "scope": settings.oidc_scopes,
        "redirect_uri": f"{settings.api_url}/api/v1/auth/callback",
        "state": redirect_to or settings.public_url,
    }
    return RedirectResponse(
        url=f"{_oidc_urls(settings)['authorize']}?{urlencode(params)}",
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )


async def _ensure_user(session: Any, email: str, display_name: str, sub: str | None) -> User:
    found = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    user: User | None = found if isinstance(found, User) else None
    if user is None:
        user = User(email=email, display_name=display_name or email.split("@", maxsplit=1)[0], oidc_sub=sub)
        session.add(user)
        await session.flush()
    user.last_login_at = utcnow()
    if sub and not user.oidc_sub:
        user.oidc_sub = sub
    return user


async def _map_groups_to_roles(session: Any, user: User, groups: list[str]) -> None:
    """Les groupes OIDC deviennent des rôles d'organisation (D11)."""
    mapping = {
        "org-admins": Role.ORG_ADMIN,
        "product-owners": Role.PROJECT_OWNER,
        "release-captains": Role.RELEASE_CAPTAIN,
        "developers": Role.DEVELOPER,
    }
    orgs = (await session.execute(select(Organization))).scalars().all()
    for org in orgs:
        role = next((mapping[g] for g in groups if g in mapping), None)
        if role is None:
            continue
        existing = (
            await session.execute(
                select(Membership).where(
                    Membership.user_id == user.id,
                    Membership.org_id == org.id,
                    Membership.project_id.is_(None),
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(Membership(user_id=user.id, org_id=org.id, role=str(role)))
        else:
            existing.role = str(role)


@router.get("/auth/callback", operation_id="authCallback")
async def callback(
    code: Annotated[str, Query()],
    state: Annotated[str, Query()],
    session: Db,
    settings: Config,
) -> RedirectResponse:
    """Échange le code contre l'identité, ouvre la session, pose le cookie signé."""
    from ..security import sign_session

    if code.startswith("dev:") and settings.dev_login_enabled:
        email = code.removeprefix("dev:")
        user = await _ensure_user(session, email, email.split("@")[0], sub=f"dev|{email}")
        groups = ["org-admins"] if email.startswith("admin") else ["developers"]
        await _map_groups_to_roles(session, user, groups)
    else:
        import httpx

        urls = _oidc_urls(settings)
        async with httpx.AsyncClient(timeout=10) as client:
            token_response = await client.post(
                urls["token"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": f"{settings.api_url}/api/v1/auth/callback",
                    "client_id": settings.oidc_client_id,
                    "client_secret": settings.oidc_client_secret,
                },
            )
            if token_response.status_code >= 400:
                raise unauthorized(f"échange OIDC refusé : {token_response.text[:200]}")
            access_token = token_response.json()["access_token"]
            info = (
                await client.get(urls["userinfo"], headers={"Authorization": f"Bearer {access_token}"})
            ).json()
        user = await _ensure_user(session, info.get("email", ""), info.get("name", ""), info.get("sub"))
        await _map_groups_to_roles(session, user, list(info.get("groups", [])))

    await record(session, None, "auth.login", target_type="user", target_id=user.id)
    cookie = sign_session({"sub": user.id, "exp": int(time.time()) + settings.session_max_age_s}, settings)
    response = RedirectResponse(
        url=state or settings.public_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )
    response.set_cookie(
        settings.session_cookie,
        cookie,
        max_age=settings.session_max_age_s,
        httponly=True,
        samesite="lax",
        secure=settings.env in {"staging", "prod"},
    )
    return response


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT, operation_id="authLogout")
async def logout(settings: Config, response: Response) -> Response:
    response.delete_cookie(settings.session_cookie)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
