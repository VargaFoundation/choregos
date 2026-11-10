# SPDX-License-Identifier: Apache-2.0
"""Session: OIDC (discovery, PKCE, signed `state`) in production, development sign-in locally.

What this router guarantees, and why each guarantee exists:

- **Discovery**: the IdP URLs come from `/.well-known/openid-configuration`. They
  used to be hard-coded on Keycloak's layout (`/protocol/openid-connect/…`) while the
  documentation promised "any OIDC issuer works".
- **A signed, single-use `state`**: it used to carry the redirect target in the clear, with no
  nonce — so neither CSRF protection of the login, nor control of the redirect (open redirect).
- **PKCE (S256)**: the authorisation code is worthless without the `code_verifier` kept in
  a ten-minute signed cookie.
- **Roles per organisation**: an OIDC group is written `choregos:<org>:<group>`. An
  unprefixed group only counts for `oidc_default_org`, otherwise for nothing. Before, any group gave the
  role on ALL the organisations of the instance.
- **Development sign-in**: off by default, refused in staging/prod, and
  ORG_ADMIN only for the e-mails listed in `dev_admin_emails`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from urllib.parse import quote, urlencode

from choregos_contracts import Role
from choregos_core import utcnow
from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from ..audit import record
from ..browser_sessions import end_session, open_session
from ..config import Settings, get_settings
from ..db.models import ApiToken, Membership, Organization, User
from ..db.org_aliases import current_slugs, organisation_by_slug_or_alias
from ..deps import Config, Db, Me, PlatformDb, resolve_project
from ..edition import group_mapper
from ..errors import ApiError, not_found, unauthorized, unprocessable
from ..logging import get_logger
from ..rbac import Permission
from ..schemas import ApiTokenCreate, ApiTokenCreated, ApiTokenDto, MeDto, MembershipDto
from ..security import generate_api_token, read_session, sign_session
from ..services.api_tokens import api_token_dto, linked_projects
from ..tenant_sign_in import OidcProvider, SignInHint, TenantSignIn, email_domain, resolve

router = APIRouter(tags=["session"])
logger = get_logger("choregos.auth")

#: Cookie that carries the PKCE `code_verifier` and the nonce between `/login` and `/callback`.
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
    """The instance provider's three endpoints: the three it is configured with, else its discovery.

    Discovery is mandatory (ADR 0100): there is no guess at a provider's layout.
    """
    if settings.oidc_authorization_endpoint:
        return {
            "authorize": settings.oidc_authorization_endpoint,
            "token": settings.oidc_token_endpoint,
            "userinfo": settings.oidc_userinfo_endpoint,
        }
    return await _discover(settings.oidc_issuer)


async def _discover(issuer: str) -> dict[str, str]:
    """Reads the provider's discovery document and checks it names this issuer.

    A failure answers 503 and is never cached, so the next sign-in tries again. A success is
    cached for the life of the process. Used for the instance's provider and for a tenant's.
    """
    issuer = issuer.rstrip("/")
    if issuer in _endpoints:
        return _endpoints[issuer]
    import httpx

    url = f"{issuer}/.well-known/openid-configuration"
    reason = ""
    found: dict[str, str] = {}
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(url)
        if response.status_code != status.HTTP_200_OK:
            reason = f"HTTP {response.status_code}"
        else:
            document = response.json()
            if not isinstance(document, dict):
                reason = "the document is not a JSON object"
            elif str(document.get("issuer", "")).rstrip("/") != issuer:
                reason = f"the document names issuer {document.get('issuer')!r}, not {issuer!r}"
            else:
                found = {
                    "authorize": str(document.get("authorization_endpoint") or ""),
                    "token": str(document.get("token_endpoint") or ""),
                    "userinfo": str(document.get("userinfo_endpoint") or ""),
                }
                missing = [name for name, value in found.items() if not value]
                if missing:
                    reason = f"the document has no {', '.join(missing)} endpoint"
    except Exception as exc:  # whatever breaks the request is an unavailable provider
        reason = f"{type(exc).__name__}: {exc}"
    if reason:
        logger.warning("OIDC discovery failed", issuer=issuer, reason=reason)
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Identity provider unavailable",
            f"OIDC discovery at {url} failed: {reason}; set CHOREGOS_OIDC_AUTHORIZATION_ENDPOINT, "
            "CHOREGOS_OIDC_TOKEN_ENDPOINT and CHOREGOS_OIDC_USERINFO_ENDPOINT if the provider "
            "publishes none",
        )
    _endpoints[issuer] = found
    return found


async def _provider_endpoints(provider: OidcProvider) -> dict[str, str]:
    """A tenant's endpoints: the three it states, else its discovery document."""
    if provider.authorization_endpoint:
        return {
            "authorize": provider.authorization_endpoint,
            "token": provider.token_endpoint,
            "userinfo": provider.userinfo_endpoint,
        }
    return await _discover(provider.issuer)


def forget_discovery() -> None:
    """Injection point for tests."""
    _endpoints.clear()


def redirection_sure(target: str | None, settings: Settings) -> str:
    """A redirect target is a relative path, or a URL of OUR front end. Nothing else."""
    if not target:
        return settings.public_url
    if target.startswith("/") and not target.startswith("//"):
        return target
    base = settings.public_url.rstrip("/")
    if target == base or target.startswith(base + "/"):
        return target
    logger.warning("redirect refused", target=target[:120])
    return settings.public_url


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _handshake(
    settings: Settings, redirect: str, *, reauth: bool = False, org: str | None = None
) -> tuple[str, str, str, str]:
    """Return (signed state, signed cookie, code_challenge, nonce); the nonce binds the two, sent to the IdP.

    `reauth` travels in the signed cookie: the IdP's answer must know that a FRESH authentication
    was asked for, so that it is required (see `callback`).
    """
    nonce = secrets.token_urlsafe(24)
    verifier, challenge = _pkce()
    expiry = int(time.time()) + OIDC_HANDSHAKE_S
    claims: dict[str, Any] = {"n": nonce, "r": redirect, "exp": expiry}
    # The tenant whose provider is asked, signed: the callback exchanges the code with that one.
    if org:
        claims["o"] = org
    state = sign_session(claims, settings, purpose="oidc-state")
    handshake: dict[str, Any] = {"n": nonce, "v": verifier, "exp": expiry}
    if reauth:
        handshake["a"] = 1
    cookie = sign_session(handshake, settings, purpose="oidc-handshake")
    return state, cookie, challenge, nonce


def _id_token_claims(id_token: Any) -> dict[str, Any] | None:
    """The claims of the ID token, or None when there is no readable one.

    Signature not verified, and that is allowed: the token comes straight from the provider's token
    endpoint, over TLS, in the code exchange (OpenID Connect Core 1.0, §3.1.3.7; ADR 0100). The
    callback checks `iss`, `aud`, `sub` and `nonce`.
    """
    if not isinstance(id_token, str) or id_token.count(".") != 2:
        return None
    try:
        session_claims = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(session_claims + "=" * (-len(session_claims) % 4)))
    except (ValueError, UnicodeDecodeError):
        return None
    return claims if isinstance(claims, dict) else None


def _auth_time(id_token: Any) -> int | None:
    """The ID token's `auth_time` claim: WHEN the user authenticated at the IdP."""
    claims = _id_token_claims(id_token)
    value = claims.get("auth_time") if claims is not None else None
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _check_id_token_claims(claims: dict[str, Any], *, issuer: str, client_id: str, nonce: object) -> None:
    """`iss`, `aud` and `nonce` of the ID token are the ones this sign-in expects (ADR 0100)."""
    if str(claims.get("iss", "")).rstrip("/") != issuer.rstrip("/"):
        raise unauthorized("the ID token was issued by another identity provider")
    audience = claims.get("aud")
    if isinstance(audience, str):
        matches = audience == client_id
    else:
        matches = isinstance(audience, list) and client_id in audience
    if not matches:
        raise unauthorized("the ID token was not issued for this client")
    if "nonce" in claims and claims["nonce"] != nonce:
        raise unauthorized("the ID token nonce does not match this sign-in")


def _poser_cookie_oidc(response: Response, cookie: str, settings: Settings) -> None:
    response.set_cookie(
        OIDC_COOKIE,
        cookie,
        max_age=OIDC_HANDSHAKE_S,
        httponly=True,
        samesite="lax",
        secure=settings.cookies_secure,
    )


@router.get("/auth/login", operation_id="authLogin")
async def login(
    request: Request,
    session: PlatformDb,
    settings: Config,
    redirect_to: Annotated[str | None, Query()] = None,
    reauth: Annotated[bool, Query()] = False,
    org: Annotated[str | None, Query(max_length=120)] = None,
    login_hint: Annotated[str | None, Query(max_length=320)] = None,
) -> RedirectResponse:
    """Redirect to the IdP. In dev, `?as=email` opens a session directly.

    `reauth=1` asks the IdP to RE-authenticate the user even if they already have a session with
    it (`prompt=login`, `max_age=0`): this is the path of a door that demands a fresh authentication,
    without which the IdP would hand control back without asking anything and `iat` would lie.

    `org` (a sign-in link per organisation) and `login_hint` (the address typed on the sign-in
    page) choose the tenant's own provider when an extension declared one (ADR 0108); otherwise
    the instance's. `login_hint` is passed on to the provider, which may prefill its form.
    """
    target = redirection_sure(redirect_to, settings)
    as_user = request.query_params.get("as")
    tenant: TenantSignIn | None = None
    if not (as_user and settings.dev_login_enabled):
        tenant = await resolve(session, SignInHint(org=org, email_domain=email_domain(login_hint)))
        if tenant is not None and org and not await _same_organisation(session, tenant.org, org):
            raise unauthorized(f"the sign-in resolver answered organisation {tenant.org!r} for {org!r}")
    if tenant is not None and tenant.start_path is not None:
        # Another protocol (SAML…), started by the extension's own route.
        forwarded = {"redirect_to": target} | ({"login_hint": login_hint} if login_hint else {})
        joiner = "&" if "?" in tenant.start_path else "?"
        return RedirectResponse(
            url=f"{tenant.start_path}{joiner}{urlencode(forwarded)}",
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )
    state, cookie, challenge, nonce = _handshake(
        settings, target, reauth=reauth, org=tenant.org if tenant is not None else None
    )
    if as_user and settings.dev_login_enabled:
        response = RedirectResponse(
            url=f"/api/v1/auth/callback?code=dev:{quote(as_user, safe='')}&state={quote(state, safe='')}",
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )
        _poser_cookie_oidc(response, cookie, settings)
        return response
    if tenant is not None and tenant.oidc is not None:
        endpoints = await _provider_endpoints(tenant.oidc)
        client_id, scopes = tenant.oidc.client_id, tenant.oidc.scopes
    else:
        endpoints = await oidc_endpoints(settings)
        client_id, scopes = settings.oidc_client_id, settings.oidc_scopes
    params = {
        "client_id": client_id,
        "response_type": "code",
        "scope": scopes,
        "redirect_uri": f"{settings.api_url}/api/v1/auth/callback",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if reauth:
        params |= {"prompt": "login", "max_age": "0"}
    if login_hint:
        params["login_hint"] = login_hint
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


def provider_name(info: dict[str, Any]) -> str:
    """The person's name as the provider gives it: `name`, else `given_name` and `family_name`.

    Empty when the provider gives none (the `profile` scope is missing): the account then keeps the
    name it has. An account invited by e-mail is named after the address's local part until then.
    """
    full = str(info.get("name") or "").strip()
    if full:
        return full[:200]
    parts = (str(info.get(claim) or "").strip() for claim in ("given_name", "family_name"))
    return " ".join(part for part in parts if part)[:200]


async def _ensure_oidc_user(
    session: Any,
    info: dict[str, Any],
    settings: Settings,
    *,
    stored_sub: str | None = None,
    tenant_org_id: str | None = None,
) -> User:
    """The console user for a provider's identity: bound to its `sub`, never to an e-mail alone.

    `stored_sub` is the subject as stored (a tenant's is namespaced); it defaults to `info["sub"]`.
    `tenant_org_id` is set by a tenant's provider, whose caller already proved the address domain is
    the tenant's (ADR 0108).

    - A returning subject keeps its account. Its e-mail follows the provider, unless the provider
      calls the new address unverified.
    - An account with that e-mail and no subject (or a development one) is linked, unless the
      provider calls the address unverified.
    - An account bound to ANOTHER subject is refused, except for a re-link: by a tenant's provider
      (the domain is the tenant's), or on the instance when `oidc_relink_verified_email` is on and
      the provider asserts `email_verified: true` (an identity provider migration). Audited.
    - A new account is not created for an address the provider calls unverified.
    (ADR 0100)
    """
    sub = stored_sub or str(info["sub"])
    email = str(info["email"])
    name = provider_name(info)
    unverified = settings.oidc_require_email_verified and info.get("email_verified") is False
    by_sub: User | None = (
        await session.execute(select(User).where(User.oidc_sub == sub))
    ).scalar_one_or_none()
    if by_sub is not None:
        by_sub.last_login_at = utcnow()
        if name:
            by_sub.display_name = name
        if by_sub.email != email and not unverified:
            taken = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
            if taken is None:
                by_sub.email = email
        return by_sub
    by_email: User | None = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if by_email is not None:
        if unverified:
            raise unauthorized("the provider says this e-mail is not verified")
        bound_elsewhere = bool(by_email.oidc_sub) and not str(by_email.oidc_sub).startswith("dev|")
        if bound_elsewhere:
            relink = tenant_org_id is not None or (
                settings.oidc_relink_verified_email and info.get("email_verified") is True
            )
            if not relink:
                raise unauthorized("this e-mail belongs to another identity at the provider")
            await record(
                session,
                None,
                "user.relink",
                org_id=tenant_org_id,
                target_type="user",
                target_id=by_email.id,
                previous_sub=by_email.oidc_sub,
            )
        by_email.oidc_sub = sub
        by_email.last_login_at = utcnow()
        if name:
            by_email.display_name = name
        return by_email
    if unverified:
        raise unauthorized("the provider says this e-mail is not verified")
    user = User(
        email=email,
        display_name=name or email.split("@", maxsplit=1)[0],
        oidc_sub=sub,
        last_login_at=utcnow(),
    )
    session.add(user)
    await session.flush()
    return user


def roles_from_groups(groups: list[str], default_org: str) -> dict[str, Role]:
    """`choregos:<org>:<group>` → role on that org. A bare group only counts for `default_org`.

    When several groups target the same organisation, the highest wins — the order of
    `GROUP_ROLES` is descending.
    """
    rank = {role: i for i, role in enumerate(GROUP_ROLES.values())}
    roles: dict[str, Role] = {}
    for group in groups:
        if group.startswith("choregos:"):
            parts = group.split(":", 2)
            org, name = (parts[1], parts[2]) if len(parts) == 3 else ("", "")
        else:
            org, name = default_org, group
        role = GROUP_ROLES.get(name)
        if not org or role is None:
            continue
        if org not in roles or rank[role] < rank[roles[org]]:
            roles[org] = role
    return roles


async def _map_groups_to_roles(
    session: Any,
    user: User,
    groups: list[str],
    settings: Settings,
    *,
    ecraser: bool = True,
    only_org: Organization | None = None,
) -> None:
    """OIDC groups become organisation roles, each on ITS organisation.

    `ecraser=False` (development sign-in): a role already set, by the bootstrap or an administrator,
    is not downgraded, and nothing is revoked. Otherwise the provider is authoritative, at each
    sign-in, for the roles it granted itself: a role it set (`memberships.source = idp`) and no
    longer maps is removed and audited (ADR 0100). A role granted by a person is never converted
    to an IdP role, so it is never revoked here.

    `only_org`: the groups come from that tenant's own provider (ADR 0108). A bare group applies
    to it, and a role on any other organisation is dropped.
    """
    if ecraser:
        # The IdP's groups, kept as it states them: an approval rule naming a group checks them
        # (ADR 0052). Replaced at each sign-in — a group withdrawn at the IdP is withdrawn here at the
        # next one. A development sign-in (`ecraser=False`) leaves them as they are.
        user.groups = sorted({str(g) for g in groups})
    # The core's mapper, or the one an extension declared (enterprise edition: a prefix per
    # organisation, SAML/SCIM groups). The indirection fits on one line and avoids forking
    # this file — that is all the C3 seam asked for, and nothing more.
    default_org = only_org.slug if only_org is not None else settings.oidc_default_org
    roles = group_mapper(roles_from_groups)(groups, default_org)
    # A group that names a FORMER slug (`choregos:<old>:<group>`, or `CHOREGOS_OIDC_DEFAULT_ORG` left at
    # the old slug) grants its role on the renamed organisation (ADR 0090). When a current slug and an
    # alias of the same organisation both appear, the higher role wins, as for two groups of one slug.
    rank = {role: i for i, role in enumerate(GROUP_ROLES.values())}
    resolu: dict[str, Role] = {}
    for name, role in roles.items():
        actuel = (await current_slugs(session, [name]))[name]
        if actuel not in resolu or rank[role] < rank[resolu[actuel]]:
            resolu[actuel] = role
    roles = resolu
    if only_org is not None:
        roles = {slug: role for slug, role in roles.items() if slug == only_org.slug}
    orgs = (
        (await session.execute(select(Organization).where(Organization.slug.in_(roles)))).scalars().all()
        if roles
        else []
    )
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
            session.add(
                Membership(
                    user_id=user.id,
                    org_id=org.id,
                    role=str(roles[org.slug]),
                    source="idp" if ecraser else "manual",
                )
            )
        elif ecraser:
            # The role follows the provider, the provenance does not: a manual row stays manual.
            existing.role = str(roles[org.slug])
    if ecraser:
        await _revoke_unmapped_memberships(session, user, set(roles), only_org=only_org)


async def _revoke_unmapped_memberships(
    session: Any, user: User, mapped: set[str], *, only_org: Organization | None = None
) -> None:
    """Removes the organisation roles the IdP granted (`source = idp`) and no longer maps.

    The sign-in is authoritative only where it could have granted: a tenant sign-in (`only_org`)
    for that organisation alone; an instance-provider sign-in for the organisations that have no
    provider of their own (a tenant's role may have come from its provider, which this sign-in
    does not speak for). The last org_admin of an organisation is kept, with a warning.
    """
    rows = (
        await session.execute(
            select(Membership, Organization.slug)
            .join(Organization, Organization.id == Membership.org_id)
            .where(
                Membership.user_id == user.id,
                Membership.project_id.is_(None),
                Membership.source == "idp",
                Organization.slug.not_in(mapped) if mapped else Organization.id.is_not(None),
            )
        )
    ).all()
    for row, slug in rows:
        if only_org is not None:
            if row.org_id != only_org.id:
                continue
        elif await resolve(session, SignInHint(org=slug)) is not None:
            continue
        if row.role == str(Role.ORG_ADMIN):
            others = (
                await session.execute(
                    select(func.count())
                    .select_from(Membership)
                    .where(
                        Membership.org_id == row.org_id,
                        Membership.project_id.is_(None),
                        Membership.role == str(Role.ORG_ADMIN),
                        Membership.id != row.id,
                    )
                )
            ).scalar_one()
            if others == 0:
                logger.warning("idp revocation skipped: last org_admin", org=slug, user=user.id)
                continue
        await session.delete(row)
        await record(
            session,
            None,
            "member.revoke",
            org_id=row.org_id,
            target_type="user",
            target_id=user.id,
            role=row.role,
            canal="oidc",
            reason="no identity-provider group maps to this organisation any more",
        )


async def _same_organisation(session: Any, first: str, second: str) -> bool:
    """Two slugs, current or former, name the same organisation (ADR 0090)."""
    a = await organisation_by_slug_or_alias(session, first)
    b = await organisation_by_slug_or_alias(session, second)
    return a is not None and b is not None and a.id == b.id


async def _tenant_user(  # noqa: PLR0913 - one sign-in, fact by fact, keyword-only
    session: Any,
    tenant_org: str,
    *,
    email: str,
    name: str,
    sub: str | None,
    groups: list[str],
    settings: Settings,
    oidc: bool = False,
    email_verified: bool | None = None,
) -> User:
    """A sign-in vouched for by a tenant's own provider, confined to that tenant (ADR 0108).

    `oidc` is set by the OIDC callback only: the identity is then bound to the provider's subject
    (ADR 0100). An extension's protocol (`terminer_la_connexion`) keeps its identity handling.

    The address must belong to a domain the resolver gives to this same tenant, or any tenant's
    provider could sign in a member of another one. The subject is namespaced by the tenant: two
    providers may issue the same `sub`.
    """
    org = await organisation_by_slug_or_alias(session, tenant_org)
    if org is None:
        raise unauthorized(f"organisation {tenant_org!r} does not exist")
    domain = email_domain(email)
    owner = await resolve(session, SignInHint(email_domain=domain)) if domain else None
    if owner is None or not await _same_organisation(session, owner.org, org.slug):
        logger.warning("tenant sign-in refused: foreign domain", org=org.slug, domain=domain)
        raise unauthorized(
            f"the address domain {domain!r} does not belong to organisation {org.slug!r}: "
            "its provider cannot sign it in"
        )
    stored = f"tenant:{org.id}:{sub}" if sub else None
    if oidc and sub:
        info = {"sub": sub, "email": email, "name": name, "email_verified": email_verified}
        user = await _ensure_oidc_user(session, info, settings, stored_sub=stored, tenant_org_id=org.id)
    else:
        user = await _ensure_user(session, email, name, stored)
    await _map_groups_to_roles(session, user, groups, settings, only_org=org)
    return user


async def _development_groups(session: Any, email: str, settings: Settings) -> list[str]:
    """In development sign-in: admin if the e-mail is listed, developer otherwise.

    In `oidc_default_org` when it is set; otherwise in every organisation of the
    bench — a bench only has one, and that is what "development" means.
    """
    name = "org-admins" if email.lower() in settings.dev_admins else "developers"
    if settings.oidc_default_org:
        return [f"choregos:{settings.oidc_default_org}:{name}"]
    orgs = (await session.execute(select(Organization.slug))).scalars().all()
    return [f"choregos:{org}:{name}" for org in orgs]


@router.get("/auth/callback", operation_id="authCallback")
async def callback(
    request: Request,
    code: Annotated[str, Query()],
    state: Annotated[str, Query()],
    session: PlatformDb,
    settings: Config,
) -> RedirectResponse:
    """Check `state` and the nonce, exchange the code (PKCE), open the session, set the signed cookie."""
    expected = read_session(state, settings, purpose="oidc-state")
    handshake = read_session(request.cookies.get(OIDC_COOKIE, ""), settings, purpose="oidc-handshake")
    if not expected or not handshake or expected.get("n") != handshake.get("n"):
        raise unauthorized("invalid or expired sign-in state: start the sign-in again")
    target = redirection_sure(str(expected.get("r") or ""), settings)
    reauth = bool(handshake.get("a"))

    if code.startswith("dev:") and settings.dev_login_enabled:
        email = code.removeprefix("dev:")
        user = await _ensure_user(session, email, email.split("@")[0], sub=f"dev|{email}")
        groups = await _development_groups(session, email, settings)
        await _map_groups_to_roles(session, user, groups, settings, ecraser=False)
        # The development sign-in IS the authentication: it happens now.
        auth_time: int | None = int(time.time())
    else:
        import httpx

        tenant_org = str(expected.get("o") or "")
        provider: OidcProvider | None = None
        if tenant_org:
            tenant = await resolve(session, SignInHint(org=tenant_org))
            if tenant is None or tenant.oidc is None:
                raise unauthorized(
                    f"organisation {tenant_org!r} no longer signs in with its own OIDC provider: "
                    "start the sign-in again"
                )
            provider = tenant.oidc
            endpoints = await _provider_endpoints(provider)
            client_id, client_secret = provider.client_id, provider.client_secret
            issuer = provider.issuer
        else:
            endpoints = await oidc_endpoints(settings)
            client_id, client_secret = settings.oidc_client_id, settings.oidc_client_secret
            issuer = settings.oidc_issuer
        async with httpx.AsyncClient(timeout=10) as client:
            token_response = await client.post(
                endpoints["token"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": f"{settings.api_url}/api/v1/auth/callback",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code_verifier": str(handshake.get("v") or ""),
                },
            )
            if token_response.status_code >= 400:
                raise unauthorized(f"OIDC exchange refused: {token_response.text[:200]}")
            tokens = token_response.json()
            access_token = tokens["access_token"]
            refresh_token = str(tokens.get("refresh_token") or "")
            claims = _id_token_claims(tokens.get("id_token"))
            if claims is not None:
                _check_id_token_claims(claims, issuer=issuer, client_id=client_id, nonce=handshake.get("n"))
            auth_time = _auth_time(tokens.get("id_token"))
            # `reauth=1` asked for `prompt=login` and `max_age=0`: the IdP MUST then return an
            # `auth_time` (OIDC Core §3.1.2.1). Without it, or if it predates the handshake,
            # the IdP handed control back without asking again — a silent SSO reconnection
            # is not a fresh authentication.
            if reauth and (auth_time is None or time.time() - auth_time > OIDC_HANDSHAKE_S):
                raise unauthorized(
                    "re-authentication was requested, but the IdP did not perform it (`auth_time` missing "
                    "or too old): try again, or check that the IdP honours `prompt=login`"
                )
            info = (
                await client.get(endpoints["userinfo"], headers={"Authorization": f"Bearer {access_token}"})
            ).json()
        sub = info.get("sub")
        if not isinstance(sub, str) or not sub:
            raise unauthorized("the identity provider did not provide a subject (`sub`)")
        if claims is not None and claims.get("sub") != sub:
            raise unauthorized("the identity provider names two different subjects (ID token and userinfo)")
        if not info.get("email"):
            raise unauthorized("the IdP did not provide an e-mail: the `email` scope is missing")
        if provider is not None:
            if provider.require_email_verified and info.get("email_verified") is False:
                raise unauthorized("the identity provider says this address is not verified")
            user = await _tenant_user(
                session,
                tenant_org,
                email=info["email"],
                name=info.get("name", ""),
                sub=info.get("sub"),
                groups=provider.mapped_groups(info.get(provider.groups_claim)),
                settings=settings,
                oidc=True,
                email_verified=info.get("email_verified"),
            )
        else:
            if sub.startswith(("dev|", "tenant:")):
                raise unauthorized("the identity provider gave a subject reserved for another sign-in")
            user = await _ensure_oidc_user(session, info, settings)
            await _map_groups_to_roles(session, user, list(info.get("groups", [])), settings)
            await _seal_refresh_token(session, user, refresh_token)

    response = await ouvrir_la_session(
        session, user, target, settings, canal="oidc", auth_time=auth_time, request=request
    )
    response.delete_cookie(OIDC_COOKIE)
    return response


async def _org_ids_of(session: Any, user_id: str) -> list[str]:
    from sqlalchemy import select

    from ..db.models import Membership

    rows = await session.execute(select(Membership.org_id).where(Membership.user_id == user_id).distinct())
    return [str(org_id) for (org_id,) in rows.all()]


async def _seal_refresh_token(session: Any, user: User, refresh_token: str) -> None:
    """The provider's refresh token of the person, sealed per person (ADR 0135 point 2): a conversation
    draws a fresh access token from it to exchange. A failure here never fails the sign-in."""
    if not refresh_token:
        return
    from ..services.identity_exchange import seal_refresh_token

    try:
        await seal_refresh_token(session, await _org_ids_of(session, user.id), user.id, refresh_token)
    except Exception as failure:  # the sign-in stands; the exchange will say `identity_unavailable`
        logger.warning("refresh token not sealed", user=user.id, error=type(failure).__name__)


async def ouvrir_la_session(
    session: Any,
    user: User,
    target: str,
    settings: Settings,
    *,
    canal: str,
    auth_time: int | None = None,
    request: Request | None = None,
) -> RedirectResponse:
    """The end of ANY sign-in: audit trace, signed cookie with `iat`, bounded redirect.

    Public so that another authentication protocol — SAML, brought by an extension — opens
    a session EXACTLY like OIDC: same keys, same duration, same cookie attributes, same
    trace. A second path written by hand would end up diverging (a forgotten `iat`, a missing `secure`)
    with nothing to signal it. `terminer_la_connexion` adds the user and their
    roles.
    """
    # a sign-in precedes the organisation resolution: a platform event
    await record(session, None, "auth.login", org_id=None, target_type="user", target_id=user.id, canal=canal)
    # `iat`: the authentication time. It is what a door demanding a RECENT authentication reads
    # (`reauth=1`), and a server-side revocation ("any session older than …").
    issued_at = int(time.time())
    expires = issued_at + settings.session_max_age_s
    # `sid`: the recorded session (ADR 0130), which its owner sees on My account and can end.
    # `request`: the browser and address are recorded at once when the caller has them; otherwise
    # with the session's first request.
    recorded = await open_session(
        session,
        user,
        channel=canal,
        expires_at=datetime.fromtimestamp(expires, tz=UTC),
        request=request,
        settings=settings,
    )
    session_claims: dict[str, Any] = {"sub": user.id, "iat": issued_at, "exp": expires, "sid": recorded.id}
    # `auth_time`: when the user REALLY authenticated, if the IdP says so. A silent
    # SSO reconnection opens a new session (fresh `iat`) on an old authentication.
    if auth_time is not None:
        session_claims["auth_time"] = auth_time
    cookie = sign_session(session_claims, settings)
    response = RedirectResponse(
        url=redirection_sure(target, settings), status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )
    response.set_cookie(
        settings.session_cookie,
        cookie,
        max_age=settings.session_max_age_s,
        httponly=True,
        samesite="lax",
        secure=settings.cookies_secure,
    )
    return response


async def terminer_la_connexion(  # noqa: PLR0913 - one sign-in, fact by fact, keyword-only
    session: Any,
    *,
    email: str,
    nom: str,  # lang: fr-ok — public API of editions (SAML), renamed with its callers
    sujet: str | None,  # lang: fr-ok — public API of editions (SAML)
    groupes: list[str],  # lang: fr-ok — public API of editions (SAML)
    cible: str,  # lang: fr-ok — public API of editions (SAML)
    canal: str,
    settings: Settings | None = None,
    org: str | None = None,
) -> RedirectResponse:
    """For an authentication protocol brought by an extension, AFTER it has verified
    the identity (assertion signature, audience, freshness): creates or finds the user,
    translates their groups into roles through the mapper in service, opens the session.

    The core verifies NOTHING of the identity passed here: that is the caller's responsibility.

    `org`: the identity comes from that tenant's own provider (a SAML IdP declared through
    `tenant_sign_in`). The sign-in is then confined to the tenant exactly as an OIDC one is
    (ADR 0108): the address domain must be the tenant's, roles land on the tenant only.
    """
    resolved_settings = settings or get_settings()
    if not email:
        raise unauthorized("the IdP did not provide an e-mail")
    if org:
        user = await _tenant_user(
            session, org, email=email, name=nom, sub=sujet, groups=list(groupes), settings=resolved_settings
        )
    else:
        user = await _ensure_user(session, email, nom, sujet)
        await _map_groups_to_roles(session, user, list(groupes), resolved_settings)
    return await ouvrir_la_session(session, user, cible, resolved_settings, canal=canal)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT, operation_id="authLogout")
async def logout(request: Request, session: PlatformDb, settings: Config, response: Response) -> Response:
    """Sign out: the cookie is cleared, and the session it named ends on the server too (ADR 0130),
    so a copy of the cookie no longer opens anything."""
    payload = read_session(request.cookies.get(settings.session_cookie, ""), settings)
    if payload and isinstance(payload.get("sid"), str) and isinstance(payload.get("sub"), str):
        await end_session(session, payload["sub"], payload["sid"])
        from ..services.identity_exchange import forget_refresh_token

        await forget_refresh_token(session, await _org_ids_of(session, payload["sub"]), payload["sub"])
    response.delete_cookie(settings.session_cookie)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


# ───────────────────────── API tokens ─────────────────────────


@router.get("/me/tokens", response_model=list[ApiTokenDto], operation_id="listMyTokens")
async def list_my_tokens(session: Db, principal: Me) -> list[ApiTokenDto]:
    rows = list(
        (
            await session.execute(
                select(ApiToken)
                .where(ApiToken.user_id == principal.user_id)
                .order_by(ApiToken.created_at.desc())
            )
        ).scalars()
    )
    projects_by_id = await linked_projects(session, {row.project_id for row in rows if row.project_id})
    return [api_token_dto(row, projects_by_id.get(row.project_id or "")) for row in rows]


@router.post(
    "/me/tokens",
    response_model=ApiTokenCreated,
    status_code=status.HTTP_201_CREATED,
    operation_id="createMyToken",
)
async def create_my_token(body: ApiTokenCreate, session: Db, principal: Me) -> ApiTokenCreated:
    """Issue a bearer token for the caller. The plain text is returned HERE only, once.

    `generate_api_token` existed with no route calling it: the documentation
    said `choregos login --token` and nothing could produce that token.
    """
    if principal.kind != "user":
        raise unauthorized("only a human session can issue a token")
    token_scopes = sorted(set(body.scopes))
    if "*" in token_scopes and len(token_scopes) > 1:
        raise unprocessable(
            "the `*` scope does not combine with others: it opens the REST API, not the MCP door"
        )
    project_id: str | None = None
    project_name: str | None = None
    if body.project is not None:
        if "*" in token_scopes:
            raise unprocessable(
                "only an MCP or A2A token (scope mcp:read, mcp:write or a2a) can be bound to a project"
            )
        # A token never sees more than its human: the project must be readable by them, and an
        # unreadable project answers like a missing one.
        try:
            project_row, org_slug = await resolve_project(session, body.project)
        except ApiError as error:
            raise not_found("Project", body.project) from error
        if not principal.can(Permission.PROJECT_READ, org_slug, project_row.slug):
            raise not_found("Project", body.project)
        project_id, project_name = project_row.id, f"{org_slug}:{project_row.slug}"
    raw, digest = generate_api_token()
    row = ApiToken(
        user_id=principal.user_id,
        name=body.name,
        hash=digest,
        scopes=token_scopes,
        project_id=project_id,
        expires_at=utcnow() + timedelta(days=body.expires_in_days) if body.expires_in_days else None,
    )
    session.add(row)
    await session.flush()
    # an API token belongs to a user, not to an organisation
    await record(
        session,
        principal,
        "token.create",
        org_id=None,
        target_type="api_token",
        target_id=row.id,
        scopes=token_scopes,
        project=project_name,
    )
    return ApiTokenCreated(**api_token_dto(row, project_name).model_dump(), token=raw)


@router.delete("/me/tokens/{id}", status_code=status.HTTP_204_NO_CONTENT, operation_id="revokeMyToken")
async def revoke_my_token(id: str, session: Db, principal: Me) -> Response:
    row = await session.get(ApiToken, id)
    if row is None or row.user_id != principal.user_id:
        raise not_found("Token", id)
    await session.delete(row)
    await record(session, principal, "token.revoke", org_id=None, target_type="api_token", target_id=id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
