# SPDX-License-Identifier: Apache-2.0
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
import json
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
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..config import Settings, get_settings
from ..db.models import ApiToken, Membership, Organization, Project, User
from ..db.org_aliases import current_slugs, organisation_by_slug_or_alias
from ..deps import Config, Db, DbPlateforme, Me, resolve_project
from ..edition import mappeur_de_groupes
from ..errors import ApiError, not_found, unauthorized, unprocessable, upstream
from ..logging import get_logger
from ..rbac import Permission
from ..schemas import ApiTokenCreate, ApiTokenCreated, ApiTokenDto, MeDto, MembershipDto
from ..security import generate_api_token, read_session, sign_session
from ..tenant_sign_in import OidcProvider, SignInHint, TenantSignIn, email_domain, resolve

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
    return await _discover(settings.oidc_issuer, keycloak_fallback=True)


async def _discover(issuer: str, *, keycloak_fallback: bool) -> dict[str, str]:
    """The discovery itself. Only the instance's provider falls back on the Keycloak layout: a
    tenant's provider whose document cannot be read fails the sign-in (502), it is never guessed.
    """
    issuer = issuer.rstrip("/")
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
        if not keycloak_fallback:
            logger.warning("tenant OIDC discovery failed", issuer=issuer, error=str(exc))
            raise upstream(
                "oidc", f"the identity provider {issuer} publishes no readable discovery document"
            ) from exc
        logger.warning(
            "découverte OIDC impossible, disposition Keycloak supposée", issuer=issuer, error=str(exc)
        )
        found = keycloak
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
    return await _discover(provider.issuer, keycloak_fallback=False)


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


def _handshake(
    settings: Settings, redirect: str, *, reauth: bool = False, org: str | None = None
) -> tuple[str, str, str]:
    """Rend (state signé, cookie signé, code_challenge). Le nonce lie les deux.

    `reauth` voyage dans le cookie signé : le retour de l'IdP doit savoir qu'une authentification
    FRAÎCHE a été demandée, pour l'exiger (voir `callback`).
    """
    nonce = secrets.token_urlsafe(24)
    verifier, challenge = _pkce()
    expiry = int(time.time()) + OIDC_HANDSHAKE_S
    claims: dict[str, Any] = {"n": nonce, "r": redirect, "exp": expiry}
    # The tenant whose provider is asked, signed: the callback exchanges the code with that one.
    if org:
        claims["o"] = org
    state = sign_session(claims, settings, purpose="oidc-state")
    poignee: dict[str, Any] = {"n": nonce, "v": verifier, "exp": expiry}
    if reauth:
        poignee["a"] = 1
    cookie = sign_session(poignee, settings, purpose="oidc-handshake")
    return state, cookie, challenge


def _auth_time(id_token: Any) -> int | None:
    """La revendication `auth_time` de l'ID token : QUAND l'utilisateur s'est authentifié chez l'IdP.

    Signature non vérifiée, et c'est permis : le jeton vient directement du point `token` de l'IdP,
    en TLS, dans l'échange du code (OpenID Connect Core 1.0, §3.1.3.7). Rien d'autre n'en est lu.
    """
    if not isinstance(id_token, str) or id_token.count(".") != 2:
        return None
    try:
        charge = id_token.split(".")[1]
        revendications = json.loads(base64.urlsafe_b64decode(charge + "=" * (-len(charge) % 4)))
    except (ValueError, UnicodeDecodeError):
        return None
    valeur = revendications.get("auth_time") if isinstance(revendications, dict) else None
    return int(valeur) if isinstance(valeur, int | float) and not isinstance(valeur, bool) else None


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
    session: DbPlateforme,
    settings: Config,
    redirect_to: Annotated[str | None, Query()] = None,
    reauth: Annotated[bool, Query()] = False,
    org: Annotated[str | None, Query(max_length=120)] = None,
    login_hint: Annotated[str | None, Query(max_length=320)] = None,
) -> RedirectResponse:
    """Redirige vers l'IdP. En dev, `?as=email` ouvre une session directement.

    `reauth=1` demande à l'IdP de RE-authentifier l'utilisateur même s'il a déjà une session chez
    lui (`prompt=login`, `max_age=0`) : c'est le chemin d'une porte qui exige une authentification
    fraîche, sans quoi l'IdP rendrait la main sans rien demander et `iat` mentirait.

    `org` (a sign-in link per organisation) and `login_hint` (the address typed on the sign-in
    page) choose the tenant's own provider when an extension declared one (ADR 0107); otherwise
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
    state, cookie, challenge = _handshake(
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


async def _map_groups_to_roles(
    session: Any,
    user: User,
    groups: list[str],
    settings: Settings,
    *,
    ecraser: bool = True,
    only_org: Organization | None = None,
) -> None:
    """Les groupes OIDC deviennent des rôles d'organisation — chacun sur SON organisation.

    `ecraser=False` (connexion de développement) : un rôle déjà posé — par l'amorçage, par
    un admin — n'est pas rétrogradé. L'IdP, lui, fait foi : ce qu'il dit remplace.

    `only_org`: the groups come from that tenant's own provider (ADR 0107). A bare group applies
    to it, and a role on any other organisation is dropped.
    """
    if ecraser:
        # The IdP's groups, kept as it states them: an approval rule naming a group checks them
        # (ADR 0052). Replaced at each sign-in — a group withdrawn at the IdP is withdrawn here at the
        # next one. A development sign-in (`ecraser=False`) leaves them as they are.
        user.groups = sorted({str(g) for g in groups})
    # Le mappeur du cœur, ou celui qu'un greffon a déclaré (édition entreprise : un préfixe par
    # organisation, des groupes SAML/SCIM). L'indirection tient en une ligne et évite un fork de
    # ce fichier — c'est tout ce que la couture C3 demandait, et rien de plus.
    default_org = only_org.slug if only_org is not None else settings.oidc_default_org
    roles = mappeur_de_groupes(roles_des_groupes)(groups, default_org)
    if not roles:
        return
    # A group that names a FORMER slug (`choregos:<old>:<group>`, or `CHOREGOS_OIDC_DEFAULT_ORG` left at
    # the old slug) grants its role on the renamed organisation (ADR 0090). When a current slug and an
    # alias of the same organisation both appear, the higher role wins, as for two groups of one slug.
    rang = {role: i for i, role in enumerate(GROUP_ROLES.values())}
    resolu: dict[str, Role] = {}
    for nom, role in roles.items():
        actuel = (await current_slugs(session, [nom]))[nom]
        if actuel not in resolu or rang[role] < rang[resolu[actuel]]:
            resolu[actuel] = role
    roles = resolu
    if only_org is not None:
        roles = {slug: role for slug, role in roles.items() if slug == only_org.slug}
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
        elif ecraser:
            existing.role = str(roles[org.slug])


async def _same_organisation(session: Any, first: str, second: str) -> bool:
    """Two slugs, current or former, name the same organisation (ADR 0090)."""
    a = await organisation_by_slug_or_alias(session, first)
    b = await organisation_by_slug_or_alias(session, second)
    return a is not None and b is not None and a.id == b.id


async def _tenant_user(
    session: Any,
    tenant_org: str,
    *,
    email: str,
    name: str,
    sub: str | None,
    groups: list[str],
    settings: Settings,
) -> User:
    """A sign-in vouched for by a tenant's own provider, confined to that tenant (ADR 0107).

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
    user = await _ensure_user(session, email, name, f"tenant:{org.id}:{sub}" if sub else None)
    await _map_groups_to_roles(session, user, groups, settings, only_org=org)
    return user


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
    session: DbPlateforme,
    settings: Config,
) -> RedirectResponse:
    """Vérifie `state` et le nonce, échange le code (PKCE), ouvre la session, pose le cookie signé."""
    attendu = read_session(state, settings, purpose="oidc-state")
    poignee = read_session(request.cookies.get(OIDC_COOKIE, ""), settings, purpose="oidc-handshake")
    if not attendu or not poignee or attendu.get("n") != poignee.get("n"):
        raise unauthorized("invalid or expired sign-in state: start the sign-in again")
    target = redirection_sure(str(attendu.get("r") or ""), settings)
    reauth = bool(poignee.get("a"))

    if code.startswith("dev:") and settings.dev_login_enabled:
        email = code.removeprefix("dev:")
        user = await _ensure_user(session, email, email.split("@")[0], sub=f"dev|{email}")
        groupes = await _groupes_de_developpement(session, email, settings)
        await _map_groups_to_roles(session, user, groupes, settings, ecraser=False)
        # La connexion de développement EST l'authentification : elle a lieu maintenant.
        auth_time: int | None = int(time.time())
    else:
        import httpx

        tenant_org = str(attendu.get("o") or "")
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
        else:
            endpoints = await oidc_endpoints(settings)
            client_id, client_secret = settings.oidc_client_id, settings.oidc_client_secret
        async with httpx.AsyncClient(timeout=10) as client:
            token_response = await client.post(
                endpoints["token"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": f"{settings.api_url}/api/v1/auth/callback",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code_verifier": str(poignee.get("v") or ""),
                },
            )
            if token_response.status_code >= 400:
                raise unauthorized(f"OIDC exchange refused: {token_response.text[:200]}")
            jetons = token_response.json()
            access_token = jetons["access_token"]
            auth_time = _auth_time(jetons.get("id_token"))
            # `reauth=1` a demandé `prompt=login` et `max_age=0` : l'IdP DOIT alors rendre un
            # `auth_time` (OIDC Core §3.1.2.1). Sans lui, ou s'il date d'avant la poignée de main,
            # l'IdP a rendu la main sans redemander l'authentification — une reconnexion SSO
            # silencieuse ne vaut pas une authentification fraîche.
            if reauth and (auth_time is None or time.time() - auth_time > OIDC_HANDSHAKE_S):
                raise unauthorized(
                    "re-authentication was requested, but the IdP did not perform it (`auth_time` missing "
                    "or too old): try again, or check that the IdP honours `prompt=login`"
                )
            info = (
                await client.get(endpoints["userinfo"], headers={"Authorization": f"Bearer {access_token}"})
            ).json()
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
            )
        else:
            user = await _ensure_user(session, info["email"], info.get("name", ""), info.get("sub"))
            await _map_groups_to_roles(session, user, list(info.get("groups", [])), settings)

    response = await ouvrir_la_session(session, user, target, settings, canal="oidc", auth_time=auth_time)
    response.delete_cookie(OIDC_COOKIE)
    return response


async def ouvrir_la_session(
    session: Any, user: User, cible: str, settings: Settings, *, canal: str, auth_time: int | None = None
) -> RedirectResponse:
    """La fin de TOUTE connexion : trace d'audit, cookie signé avec `iat`, redirection bornée.

    Publique pour qu'un autre protocole d'authentification — SAML, apporté par un greffon — ouvre
    une session EXACTEMENT comme OIDC : mêmes clés, même durée, mêmes attributs de cookie, même
    trace. Un second chemin écrit à la main finirait par diverger (un `iat` oublié, un `secure`
    manquant) sans que rien ne le signale. `terminer_la_connexion` y ajoute l'utilisateur et ses
    rôles.
    """
    # une connexion précède la résolution d'organisation : événement de plateforme
    await record(session, None, "auth.login", org_id=None, target_type="user", target_id=user.id, canal=canal)
    # `iat` : l'heure d'authentification. C'est elle que lit une porte qui exige une authentification
    # RÉCENTE (`reauth=1`), et une révocation côté serveur (« toute session antérieure à … »).
    maintenant = int(time.time())
    fin = maintenant + settings.session_max_age_s
    charge: dict[str, Any] = {"sub": user.id, "iat": maintenant, "exp": fin}
    # `auth_time` : quand l'utilisateur s'est VRAIMENT authentifié, si l'IdP le dit. Une reconnexion
    # SSO silencieuse ouvre une session neuve (`iat` frais) sur une authentification ancienne.
    if auth_time is not None:
        charge["auth_time"] = auth_time
    cookie = sign_session(charge, settings)
    response = RedirectResponse(
        url=redirection_sure(cible, settings), status_code=status.HTTP_307_TEMPORARY_REDIRECT
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
    nom: str,
    sujet: str | None,
    groupes: list[str],
    cible: str,
    canal: str,
    settings: Settings | None = None,
    org: str | None = None,
) -> RedirectResponse:
    """Pour un protocole d'authentification apporté par un greffon, APRÈS qu'il a vérifié
    l'identité (signature d'assertion, audience, fraîcheur) : crée ou retrouve l'utilisateur,
    traduit ses groupes en rôles par le mappeur en service, ouvre la session.

    Le cœur ne vérifie RIEN de l'identité passée ici : c'est la responsabilité de l'appelant.

    `org`: the identity comes from that tenant's own provider (a SAML IdP declared through
    `tenant_sign_in`). The sign-in is then confined to the tenant exactly as an OIDC one is
    (ADR 0107): the address domain must be the tenant's, roles land on the tenant only.
    """
    reglages = settings or get_settings()
    if not email:
        raise unauthorized("the IdP did not provide an e-mail")
    if org:
        user = await _tenant_user(
            session, org, email=email, name=nom, sub=sujet, groups=list(groupes), settings=reglages
        )
    else:
        user = await _ensure_user(session, email, nom, sujet)
        await _map_groups_to_roles(session, user, list(groupes), reglages)
    return await ouvrir_la_session(session, user, cible, reglages, canal=canal)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT, operation_id="authLogout")
async def logout(settings: Config, response: Response) -> Response:
    response.delete_cookie(settings.session_cookie)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


# ───────────────────────── jetons d'API ─────────────────────────


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
    projets = await _projets_lies(session, {row.project_id for row in rows if row.project_id})
    return [_token_dto(row, projets.get(row.project_id or "")) for row in rows]


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
        raise unauthorized("only a human session can issue a token")
    portees = sorted(set(body.scopes))
    if "*" in portees and len(portees) > 1:
        raise unprocessable(
            "the `*` scope does not combine with others: it opens the REST API, not the MCP door"
        )
    projet_id: str | None = None
    projet_nom: str | None = None
    if body.project is not None:
        if "*" in portees:
            raise unprocessable(
                "only an MCP or A2A token (scope mcp:read, mcp:write or a2a) can be bound to a project"
            )
        # Un jeton ne voit jamais plus que son humain : le projet doit lui être lisible, et un
        # projet illisible répond comme un projet inexistant.
        try:
            projet, org_slug = await resolve_project(session, body.project)
        except ApiError as erreur:
            raise not_found("Project", body.project) from erreur
        if not principal.can(Permission.PROJECT_READ, org_slug, projet.slug):
            raise not_found("Project", body.project)
        projet_id, projet_nom = projet.id, f"{org_slug}:{projet.slug}"
    raw, digest = generate_api_token()
    row = ApiToken(
        user_id=principal.user_id,
        name=body.name,
        hash=digest,
        scopes=portees,
        project_id=projet_id,
        expires_at=utcnow() + timedelta(days=body.expires_in_days) if body.expires_in_days else None,
    )
    session.add(row)
    await session.flush()
    # un jeton d'API appartient à un utilisateur, pas à une organisation
    await record(
        session,
        principal,
        "token.create",
        org_id=None,
        target_type="api_token",
        target_id=row.id,
        scopes=portees,
        project=projet_nom,
    )
    return ApiTokenCreated(**_token_dto(row, projet_nom).model_dump(), token=raw)


@router.delete("/me/tokens/{id}", status_code=status.HTTP_204_NO_CONTENT, operation_id="revokeMyToken")
async def revoke_my_token(id: str, session: Db, principal: Me) -> Response:
    row = await session.get(ApiToken, id)
    if row is None or row.user_id != principal.user_id:
        raise not_found("Token", id)
    await session.delete(row)
    await record(session, principal, "token.revoke", org_id=None, target_type="api_token", target_id=id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _token_dto(row: ApiToken, projet: str | None = None) -> ApiTokenDto:
    return ApiTokenDto(
        id=row.id,
        name=row.name,
        created_at=row.created_at,
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
        scopes=list(row.scopes or ["*"]),
        project=projet,
        last_client=row.last_client,
    )


async def _projets_lies(session: AsyncSession, ids: set[str]) -> dict[str, str]:
    """`{id: "org:slug"}` des projets auxquels des jetons sont liés."""
    if not ids:
        return {}
    rows = await session.execute(
        select(Project.id, Organization.slug, Project.slug)
        .join(Organization, Project.org_id == Organization.id)
        .where(Project.id.in_(ids))
    )
    return {pid: f"{org}:{slug}" for pid, org, slug in rows}
