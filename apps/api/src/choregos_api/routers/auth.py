"""Session : OIDC (discovery, PKCE, `state` signé) en production, connexion de développement en local.

Ce que ce routeur garantit, et pourquoi chaque garantie existe :

- **Discovery** : les URL de l'IdP viennent de `/.well-known/openid-configuration`. Elles
  étaient codées sur la disposition de Keycloak (`/protocol/openid-connect/…`) pendant que la
  documentation promettait « any OIDC issuer works ».
- **`state` signé et à usage unique** : il portait la cible de redirection en clair, sans
  nonce — donc ni protection CSRF du login, ni contrôle de la redirection (open redirect).
- **PKCE (S256)** : le code d'autorisation ne vaut rien sans le `code_verifier` gardé dans
  un cookie signé de dix minutes.
- **Rôles par organisation** : un groupe OIDC s'écrit `choregos:<org>:<groupe>`. Un groupe
  non préfixé ne vaut que pour `oidc_default_org`, sinon rien. Avant, tout groupe donnait le
  rôle sur TOUTES les organisations de l'instance.
- **Connexion de développement** : éteinte par défaut, refusée en staging/prod, et
  ORG_ADMIN seulement pour les e-mails listés dans `dev_admin_emails`.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from datetime import timedelta
from typing import Annotated, Any
from urllib.parse import quote, urlencode

from choregos_contracts import Role
from choregos_core import utcnow
from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from ..audit import record
from ..config import Settings
from ..db.models import ApiToken, Membership, Organization, User
from ..deps import Config, Db, Me
from ..errors import not_found, unauthorized
from ..logging import get_logger
from ..schemas import ApiTokenCreate, ApiTokenCreated, ApiTokenDto, MeDto, MembershipDto
from ..security import generate_api_token, read_session, sign_session

router = APIRouter(tags=["session"])
logger = get_logger("choregos.auth")

#: Cookie qui porte le `code_verifier` PKCE et le nonce entre `/login` et `/callback`.
OIDC_COOKIE = "choregos_oidc"
OIDC_HANDSHAKE_S = 600

GROUP_ROLES: dict[str, Role] = {
    "org-admins": Role.ORG_ADMIN,
    "product-owners": Role.PROJECT_OWNER,
    "release-captains": Role.RELEASE_CAPTAIN,
    "developers": Role.DEVELOPER,
    "viewers": Role.VIEWER,
}


@router.get("/me", response_model=MeDto, operation_id="getMe")
async def get_me(session: Db, principal: Me) -> MeDto:
    user = await session.get(User, principal.user_id)
    if user is None:
        raise unauthorized()
    memberships = [
        MembershipDto(org=org, role=role, user_id=user.id, email=user.email)
        for org, role in principal.org_roles.items()
    ]
    for qualified, role in principal.project_roles.items():
        org, _, slug = qualified.partition("/")
        memberships.append(
            MembershipDto(org=org, project_slug=slug, role=role, user_id=user.id, email=user.email)
        )
    return MeDto(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        oidc_sub=user.oidc_sub,
        last_login_at=user.last_login_at,
        memberships=memberships,
    )


# ───────────────────────── OIDC : discovery, redirection, PKCE ─────────────────────────

_endpoints: dict[str, dict[str, str]] = {}


async def oidc_endpoints(settings: Settings) -> dict[str, str]:
    """Les trois URL de l'IdP, lues une fois sur son document de découverte.

    Si le document est injoignable, on retombe sur la disposition de Keycloak en le
    disant : un IdP qui ne publie pas sa découverte est rare, un Keycloak dont le réseau
    tousse au démarrage l'est moins.
    """
    issuer = settings.oidc_issuer.rstrip("/")
    if issuer in _endpoints:
        return _endpoints[issuer]
    import httpx

    keycloak = {
        "authorize": f"{issuer}/protocol/openid-connect/auth",
        "token": f"{issuer}/protocol/openid-connect/token",
        "userinfo": f"{issuer}/protocol/openid-connect/userinfo",
    }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            document = (await client.get(f"{issuer}/.well-known/openid-configuration")).json()
        found = {
            "authorize": str(document["authorization_endpoint"]),
            "token": str(document["token_endpoint"]),
            "userinfo": str(document.get("userinfo_endpoint") or keycloak["userinfo"]),
        }
    except Exception as exc:
        logger.warning(
            "découverte OIDC impossible, disposition Keycloak supposée", issuer=issuer, error=str(exc)
        )
        found = keycloak
    _endpoints[issuer] = found
    return found


def oublier_la_decouverte() -> None:
    """Point d'injection pour les tests."""
    _endpoints.clear()


def redirection_sure(target: str | None, settings: Settings) -> str:
    """Une cible de redirection est un chemin relatif, ou une URL de NOTRE front. Rien d'autre."""
    if not target:
        return settings.public_url
    if target.startswith("/") and not target.startswith("//"):
        return target
    base = settings.public_url.rstrip("/")
    if target == base or target.startswith(base + "/"):
        return target
    logger.warning("redirection refusée", target=target[:120])
    return settings.public_url


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _handshake(settings: Settings, redirect: str) -> tuple[str, str, str]:
    """Rend (state signé, cookie signé, code_challenge). Le nonce lie les deux."""
    nonce = secrets.token_urlsafe(24)
    verifier, challenge = _pkce()
    expiry = int(time.time()) + OIDC_HANDSHAKE_S
    state = sign_session({"n": nonce, "r": redirect, "exp": expiry}, settings)
    cookie = sign_session({"n": nonce, "v": verifier, "exp": expiry}, settings)
    return state, cookie, challenge


def _poser_cookie_oidc(response: Response, cookie: str, settings: Settings) -> None:
    response.set_cookie(
        OIDC_COOKIE,
        cookie,
        max_age=OIDC_HANDSHAKE_S,
        httponly=True,
        samesite="lax",
        secure=settings.env in {"staging", "prod"},
    )


@router.get("/auth/login", operation_id="authLogin")
async def login(
    request: Request,
    settings: Config,
    redirect_to: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    """Redirige vers l'IdP. En dev, `?as=email` ouvre une session directement."""
    target = redirection_sure(redirect_to, settings)
    state, cookie, challenge = _handshake(settings, target)
    as_user = request.query_params.get("as")
    if as_user and settings.dev_login_enabled:
        response = RedirectResponse(
            url=f"/api/v1/auth/callback?code=dev:{quote(as_user, safe='')}&state={quote(state, safe='')}",
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )
        _poser_cookie_oidc(response, cookie, settings)
        return response
    endpoints = await oidc_endpoints(settings)
    params = {
        "client_id": settings.oidc_client_id,
        "response_type": "code",
        "scope": settings.oidc_scopes,
        "redirect_uri": f"{settings.api_url}/api/v1/auth/callback",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    response = RedirectResponse(
        url=f"{endpoints['authorize']}?{urlencode(params)}", status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )
    _poser_cookie_oidc(response, cookie, settings)
    return response


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


def roles_des_groupes(groups: list[str], default_org: str) -> dict[str, Role]:
    """`choregos:<org>:<groupe>` → rôle sur cette org. Un groupe nu ne vaut que pour `default_org`.

    Quand plusieurs groupes visent la même organisation, le plus élevé gagne — l'ordre de
    `GROUP_ROLES` est décroissant.
    """
    rang = {role: i for i, role in enumerate(GROUP_ROLES.values())}
    roles: dict[str, Role] = {}
    for group in groups:
        if group.startswith("choregos:"):
            parts = group.split(":", 2)
            org, nom = (parts[1], parts[2]) if len(parts) == 3 else ("", "")
        else:
            org, nom = default_org, group
        role = GROUP_ROLES.get(nom)
        if not org or role is None:
            continue
        if org not in roles or rang[role] < rang[roles[org]]:
            roles[org] = role
    return roles


async def _map_groups_to_roles(session: Any, user: User, groups: list[str], settings: Settings) -> None:
    """Les groupes OIDC deviennent des rôles d'organisation — chacun sur SON organisation."""
    roles = roles_des_groupes(groups, settings.oidc_default_org)
    if not roles:
        return
    orgs = (await session.execute(select(Organization).where(Organization.slug.in_(roles)))).scalars().all()
    for org in orgs:
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
            session.add(Membership(user_id=user.id, org_id=org.id, role=str(roles[org.slug])))
        else:
            existing.role = str(roles[org.slug])


async def _groupes_de_developpement(session: Any, email: str, settings: Settings) -> list[str]:
    """En connexion de développement : admin si l'e-mail est listé, développeur sinon.

    Dans `oidc_default_org` quand elle est définie ; sinon dans toutes les organisations du
    banc — un banc n'en a qu'une, et c'est ce que « développement » veut dire.
    """
    nom = "org-admins" if email.lower() in settings.dev_admins else "developers"
    if settings.oidc_default_org:
        return [f"choregos:{settings.oidc_default_org}:{nom}"]
    orgs = (await session.execute(select(Organization.slug))).scalars().all()
    return [f"choregos:{org}:{nom}" for org in orgs]


@router.get("/auth/callback", operation_id="authCallback")
async def callback(
    request: Request,
    code: Annotated[str, Query()],
    state: Annotated[str, Query()],
    session: Db,
    settings: Config,
) -> RedirectResponse:
    """Vérifie `state` et le nonce, échange le code (PKCE), ouvre la session, pose le cookie signé."""
    attendu = read_session(state, settings)
    poignee = read_session(request.cookies.get(OIDC_COOKIE, ""), settings)
    if not attendu or not poignee or attendu.get("n") != poignee.get("n"):
        raise unauthorized("état de connexion invalide ou périmé : recommencer la connexion")
    target = redirection_sure(str(attendu.get("r") or ""), settings)

    if code.startswith("dev:") and settings.dev_login_enabled:
        email = code.removeprefix("dev:")
        user = await _ensure_user(session, email, email.split("@")[0], sub=f"dev|{email}")
        await _map_groups_to_roles(
            session, user, await _groupes_de_developpement(session, email, settings), settings
        )
    else:
        import httpx

        endpoints = await oidc_endpoints(settings)
        async with httpx.AsyncClient(timeout=10) as client:
            token_response = await client.post(
                endpoints["token"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": f"{settings.api_url}/api/v1/auth/callback",
                    "client_id": settings.oidc_client_id,
                    "client_secret": settings.oidc_client_secret,
                    "code_verifier": str(poignee.get("v") or ""),
                },
            )
            if token_response.status_code >= 400:
                raise unauthorized(f"échange OIDC refusé : {token_response.text[:200]}")
            access_token = token_response.json()["access_token"]
            info = (
                await client.get(endpoints["userinfo"], headers={"Authorization": f"Bearer {access_token}"})
            ).json()
        if not info.get("email"):
            raise unauthorized("l'IdP n'a pas fourni d'e-mail : le scope `email` manque")
        user = await _ensure_user(session, info["email"], info.get("name", ""), info.get("sub"))
        await _map_groups_to_roles(session, user, list(info.get("groups", [])), settings)

    await record(session, None, "auth.login", target_type="user", target_id=user.id)
    cookie = sign_session({"sub": user.id, "exp": int(time.time()) + settings.session_max_age_s}, settings)
    response = RedirectResponse(url=target, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    response.set_cookie(
        settings.session_cookie,
        cookie,
        max_age=settings.session_max_age_s,
        httponly=True,
        samesite="lax",
        secure=settings.env in {"staging", "prod"},
    )
    response.delete_cookie(OIDC_COOKIE)
    return response


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT, operation_id="authLogout")
async def logout(settings: Config, response: Response) -> Response:
    response.delete_cookie(settings.session_cookie)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


# ───────────────────────── jetons d'API ─────────────────────────


@router.get("/me/tokens", response_model=list[ApiTokenDto], operation_id="listMyTokens")
async def list_my_tokens(session: Db, principal: Me) -> list[ApiTokenDto]:
    rows = (
        await session.execute(
            select(ApiToken).where(ApiToken.user_id == principal.user_id).order_by(ApiToken.created_at.desc())
        )
    ).scalars()
    return [_token_dto(row) for row in rows]


@router.post(
    "/me/tokens",
    response_model=ApiTokenCreated,
    status_code=status.HTTP_201_CREATED,
    operation_id="createMyToken",
)
async def create_my_token(body: ApiTokenCreate, session: Db, principal: Me) -> ApiTokenCreated:
    """Émet un jeton porteur pour l'appelant. Le clair n'est rendu qu'ICI, une fois.

    `generate_api_token` existait sans qu'aucune route ne l'appelle : la documentation
    disait `choregos login --token` et rien ne pouvait produire ce jeton.
    """
    if principal.kind != "user":
        raise unauthorized("seule une session humaine peut émettre un jeton")
    raw, digest = generate_api_token()
    row = ApiToken(
        user_id=principal.user_id,
        name=body.name,
        hash=digest,
        scopes=["*"],
        expires_at=utcnow() + timedelta(days=body.expires_in_days) if body.expires_in_days else None,
    )
    session.add(row)
    await session.flush()
    await record(session, principal, "token.create", target_type="api_token", target_id=row.id)
    return ApiTokenCreated(**_token_dto(row).model_dump(), token=raw)


@router.delete("/me/tokens/{id}", status_code=status.HTTP_204_NO_CONTENT, operation_id="revokeMyToken")
async def revoke_my_token(id: str, session: Db, principal: Me) -> Response:
    row = await session.get(ApiToken, id)
    if row is None or row.user_id != principal.user_id:
        raise not_found("Jeton", id)
    await session.delete(row)
    await record(session, principal, "token.revoke", target_type="api_token", target_id=id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _token_dto(row: ApiToken) -> ApiTokenDto:
    return ApiTokenDto(
        id=row.id,
        name=row.name,
        created_at=row.created_at,
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
    )
