# SPDX-License-Identifier: Apache-2.0
"""Remote agents over A2A (ADR 0062): the organisation's credentials, and the discovery of a card.

OAuth credentials follow the authorization-code flow with PKCE, against a client the platform
registered itself (Dynamic Client Registration, RFC 7591) at the endpoint the card advertises:

1. `POST …/remote-credentials` reads the card and registers the client; the credential is `pending`.
2. `POST …/{name}/authorize` returns the provider's authorisation URL and sets a signed cookie that
   carries the PKCE verifier (the handshake pattern of `routers/auth.py`). The administrator's browser
   goes there.
3. The provider redirects to `GET /remote-credentials/oauth/callback`: the code is exchanged, the
   REFRESH token is sealed in the secret store and never returned, `authorised_as` is recorded.

Everything needs `agent:manage` (org_admin). Whoever authorises is who the remote agent acts as: a
dedicated, least-privileged account is the recommendation (docs/integrations.md).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from collections.abc import AsyncIterator
from typing import Annotated, Any
from urllib.parse import urlencode, urlsplit

import httpx
from choregos_adapters.a2a import AUTH_BEARER, AUTH_NONE, AUTH_OAUTH, AgentCard
from choregos_core import utcnow
from choregos_core.redaction import remember_secret
from fastapi import APIRouter, Depends, Path, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import Organization, RemoteCredential
from ..deps import Config, Db, Me
from ..errors import conflict, forbidden, not_found, unprocessable, upstream
from ..rbac import Permission
from ..schemas import (
    A2aRuntime,
    AgentCreate,
    AgentSpec,
    RemoteAgentDiscover,
    RemoteAgentDiscovery,
    RemoteCardSummary,
    RemoteCredentialAuthorization,
    RemoteCredentialCreate,
    RemoteCredentialDto,
    RemoteSkillInfo,
)
from ..secret_store import SecretStoreUnavailable, get_secret_store
from ..security import read_session, sign_session
from ..services.egress import egress_scope, ensure_or_409, remote_agent_targets
from ..services.remote_agents import a2a_transport, credential_named, read_card
from ..services.remote_tokens import secret_name, store_refresh_token


async def _egress(session: Db, settings: Config, org: str | None = None) -> AsyncIterator[None]:
    """Every outbound A2A call of these routes (card, DCR, token) is checked against the
    organisation's egress allowlist (S39-22). The OAuth callback has no `{org}`: it sets its own."""
    if org is None:
        yield
        return
    organisation = (
        await session.execute(select(Organization).where(Organization.slug == org))
    ).scalar_one_or_none()
    if organisation is None:
        yield
        return
    async with egress_scope(session, organisation, "api", "remote-agent", settings):
        yield


router = APIRouter(tags=["remote-agents"], dependencies=[Depends(_egress, scope="function")])

Name = Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]{1,62}$")]
OAUTH_COOKIE = "choregos_remote_oauth"
HANDSHAKE_S = 600
CALLBACK_PATH = "/api/v1/remote-credentials/oauth/callback"
#: Atlassian's OAuth 2.0 (3LO) wants these two on the authorisation request.
ATLASSIAN_AUTHORIZE = {"audience": "api.atlassian.com", "prompt": "consent"}
ATLASSIAN_ME = "https://api.atlassian.com/me"


def _manage(principal: Me, org: str) -> None:
    if not principal.can(Permission.AGENT_MANAGE, org):
        raise forbidden("remote agents and their credentials are managed by the org_admin role")


async def _organisation(session: AsyncSession, slug: str) -> Organization:
    found = (
        await session.execute(select(Organization).where(Organization.slug == slug))
    ).scalar_one_or_none()
    if found is None:
        raise not_found("Organisation", slug)
    return found


async def _credential(session: AsyncSession, organisation: Organization, name: str) -> RemoteCredential:
    row = await credential_named(session, organisation.id, name)
    if row is None:
        raise not_found("RemoteCredential", name)
    return row


def _dto(row: RemoteCredential) -> RemoteCredentialDto:
    config = row.config or {}
    return RemoteCredentialDto(
        name=row.name,
        type=row.type,
        status=row.status,
        authorised_as=row.authorised_as,
        scopes=list(config.get("scopes") or []),
        card_url=config.get("card_url"),
        host=config.get("host"),
        created_by=row.created_by,
        created_at=row.created_at,
        last_refresh_at=row.last_refresh_at,
        expires_at=row.expires_at,
        last_error=row.last_error,
    )


def _redirect_uri(settings: Any) -> str:
    return f"{str(settings.api_url).rstrip('/')}{CALLBACK_PATH}"


def _https(url: str, what: str) -> str:
    if urlsplit(url).scheme != "https":
        raise unprocessable(f"the {what} must be https")
    return url


# ───────────────────────────────────── credentials ─────────────────────────────────────


@router.get(
    "/orgs/{org}/remote-credentials",
    response_model=list[RemoteCredentialDto],
    operation_id="listRemoteCredentials",
)
async def list_remote_credentials(org: str, session: Db, principal: Me) -> list[RemoteCredentialDto]:
    _manage(principal, org)
    organisation = await _organisation(session, org)
    rows = (
        await session.execute(
            select(RemoteCredential)
            .where(RemoteCredential.org_id == organisation.id)
            .order_by(RemoteCredential.name)
        )
    ).scalars()
    return [_dto(row) for row in rows]


@router.post(
    "/orgs/{org}/remote-credentials",
    response_model=RemoteCredentialDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="createRemoteCredential",
)
async def create_remote_credential(
    org: str, body: RemoteCredentialCreate, session: Db, principal: Me, settings: Config
) -> RemoteCredentialDto:
    _manage(principal, org)
    organisation = await _organisation(session, org)
    if await credential_named(session, organisation.id, body.name) is not None:
        raise conflict(f"remote credential `{body.name}` already exists in {org}")
    row = RemoteCredential(
        org_id=organisation.id,
        name=body.name,
        type=body.type,
        config={},
        status="pending",
        created_by=principal.email,
    )
    store = get_secret_store()
    if body.type == AUTH_OAUTH:
        if not body.card_url:
            raise unprocessable("an OAuth credential is created from a card: give `card_url`")
        if not store.available():
            from ..secret_store import NO_KEY_MESSAGE

            raise conflict(NO_KEY_MESSAGE)
        card = await read_card(body.card_url)
        row.config = await _register_client(session, organisation, row, card, body.card_url, settings, store)
    elif body.type == AUTH_BEARER:
        if not body.token:
            raise unprocessable("a bearer credential needs its `token`")
        if not store.available():
            from ..secret_store import NO_KEY_MESSAGE

            raise conflict(NO_KEY_MESSAGE)
        remember_secret(body.token)
        try:
            await store.put(
                session, organisation.id, secret_name(body.name, "token"), body.token, principal.email
            )
        except SecretStoreUnavailable as exc:
            raise conflict(str(exc)) from exc
        row.secret_ref = f"store:{secret_name(body.name, 'token')}"
        row.status = "connected"
        row.config = {"card_url": body.card_url, "host": _host(body.card_url)}
    else:  # none
        row.status = "connected"
        row.config = {"card_url": body.card_url, "host": _host(body.card_url)}
    session.add(row)
    await session.flush()
    await record(
        session,
        principal,
        "remote_credential.create",
        org_id=organisation.id,
        target_type="remote_credential",
        target_id=row.id,
        name=body.name,
        type=body.type,
    )
    return _dto(row)


def _host(url: str | None) -> str | None:
    return (urlsplit(url).hostname or None) if url else None


async def _register_client(
    session: AsyncSession,
    organisation: Organization,
    row: RemoteCredential,
    card: AgentCard,
    card_url: str,
    settings: Any,
    store: Any,
) -> dict[str, Any]:
    """Dynamic Client Registration (RFC 7591) at the endpoint the card advertises."""
    if AUTH_OAUTH not in card.auth_types or not card.token_url or not card.authorization_url:
        raise unprocessable("the card does not offer the OAuth authorization-code flow")
    if card.dcr_url is None:
        raise unprocessable(
            "the card advertises no Dynamic Client Registration endpoint: this platform cannot register "
            "itself at that agent"
        )
    _https(card.dcr_url, "registration endpoint")
    redirect = _redirect_uri(settings)
    payload = {
        "client_name": "Choregos",
        "redirect_uris": [redirect],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": " ".join(card.scopes),
    }
    try:
        async with httpx.AsyncClient(transport=a2a_transport(), timeout=20, follow_redirects=False) as client:
            response = await client.post(card.dcr_url, json=payload)
    except httpx.HTTPError as exc:
        raise upstream(
            "remote-agent", f"the registration endpoint is unreachable ({type(exc).__name__})"
        ) from exc
    if response.status_code >= 400:
        raise upstream("remote-agent", f"the registration endpoint refused (HTTP {response.status_code})")
    registered = response.json()
    client_id = registered.get("client_id")
    if not client_id:
        raise upstream("remote-agent", "the registration endpoint answered without a client_id")
    config: dict[str, Any] = {
        "card_url": card_url,
        "host": card.host,
        "client_id": str(client_id),
        "token_url": _https(card.token_url, "token endpoint"),
        "authorization_url": _https(card.authorization_url, "authorization endpoint"),
        "scopes": list(card.scopes),
        "redirect_uri": redirect,
    }
    if registered.get("client_secret"):
        name = secret_name(row.name, "client_secret")
        remember_secret(str(registered["client_secret"]))
        await store.put(session, organisation.id, name, str(registered["client_secret"]), row.created_by)
        config["client_secret_name"] = name
    return config


@router.post(
    "/orgs/{org}/remote-credentials/{name}/authorize",
    response_model=RemoteCredentialAuthorization,
    operation_id="authorizeRemoteCredential",
)
async def authorize_remote_credential(
    org: str, name: Name, response: Response, session: Db, principal: Me, settings: Config
) -> RemoteCredentialAuthorization:
    """Where to send the browser; the PKCE verifier waits in a signed, httpOnly cookie."""
    _manage(principal, org)
    organisation = await _organisation(session, org)
    row = await _credential(session, organisation, name)
    if row.type != AUTH_OAUTH:
        raise unprocessable("only an OAuth credential is authorised in a browser")
    if row.status == "revoked":
        raise conflict(f"the remote credential `{name}` is revoked")
    config = row.config or {}
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode().rstrip("=")
    )
    nonce = secrets.token_urlsafe(24)
    expiry = int(time.time()) + HANDSHAKE_S
    state = sign_session({"n": nonce, "o": org, "c": name, "exp": expiry}, settings)
    cookie = sign_session({"n": nonce, "v": verifier, "exp": expiry}, settings)
    query: dict[str, str] = {
        "response_type": "code",
        "client_id": str(config["client_id"]),
        "redirect_uri": str(config["redirect_uri"]),
        "scope": " ".join(config.get("scopes") or []),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if (urlsplit(str(config["authorization_url"])).hostname or "").endswith("atlassian.com"):
        query.update(ATLASSIAN_AUTHORIZE)
    response.set_cookie(
        OAUTH_COOKIE,
        cookie,
        max_age=HANDSHAKE_S,
        httponly=True,
        samesite="lax",
        secure=settings.cookies_secure,
    )
    await record(
        session,
        principal,
        "remote_credential.authorize_start",
        org_id=organisation.id,
        target_type="remote_credential",
        target_id=row.id,
        name=name,
    )
    return RemoteCredentialAuthorization(redirect_url=f"{config['authorization_url']}?{urlencode(query)}")


@router.get("/remote-credentials/oauth/callback", operation_id="remoteCredentialCallback")
async def remote_credential_callback(
    request: Request,
    code: Annotated[str, Query()],
    state: Annotated[str, Query()],
    session: Db,
    principal: Me,
    settings: Config,
) -> RedirectResponse:
    """The provider's redirect: verify state and cookie, exchange the code, seal the refresh token."""
    expected = read_session(state, settings)
    handshake = read_session(request.cookies.get(OAUTH_COOKIE, ""), settings)
    now = time.time()
    if (
        not expected
        or not handshake
        or expected.get("n") != handshake.get("n")
        or float(expected.get("exp", 0)) < now
    ):
        raise forbidden("invalid or expired authorisation state: start the connection again")
    org, name = str(expected.get("o") or ""), str(expected.get("c") or "")
    _manage(principal, org)
    organisation = await _organisation(session, org)
    row = await _credential(session, organisation, name)
    config = row.config or {}
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": str(config.get("redirect_uri") or ""),
        "client_id": str(config.get("client_id") or ""),
        "code_verifier": str(handshake.get("v") or ""),
    }
    store = get_secret_store()
    if config.get("client_secret_name"):
        form["client_secret"] = (
            await store.get_many(session, organisation.id, [config["client_secret_name"]])
        )[config["client_secret_name"]]
    async with egress_scope(session, organisation, "api", "remote-agent", settings):
        try:
            async with httpx.AsyncClient(
                transport=a2a_transport(), timeout=20, follow_redirects=False
            ) as client:
                exchanged = await client.post(str(config.get("token_url")), data=form)
                tokens = exchanged.json() if exchanged.status_code < 400 else {}
                if exchanged.status_code >= 400 or not tokens.get("refresh_token"):
                    detail = (
                        f"HTTP {exchanged.status_code}"
                        if exchanged.status_code >= 400
                        else "no refresh token (is `offline_access` granted?)"
                    )
                    raise upstream("remote-agent", f"the provider did not give a refresh token: {detail}")
                access = str(tokens.get("access_token") or "")
                remember_secret(access)
                remember_secret(str(tokens["refresh_token"]))
                who = await _who(client, config, tokens, access)
        except httpx.HTTPError as exc:
            raise upstream(
                "remote-agent", f"the token endpoint is unreachable ({type(exc).__name__})"
            ) from exc
    await store_refresh_token(session, store, row, str(tokens["refresh_token"]), principal.email)
    row.authorised_as = who
    row.status, row.last_error = "connected", None
    row.last_refresh_at = utcnow()
    await record(
        session,
        principal,
        "remote_credential.authorize",
        org_id=organisation.id,
        target_type="remote_credential",
        target_id=row.id,
        name=name,
        authorised_as=who,
    )
    target = f"{str(settings.public_url).rstrip('/')}/admin/remote-agents?connected={name}"
    redirect = RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)
    redirect.delete_cookie(OAUTH_COOKIE)
    return redirect


async def _who(
    client: httpx.AsyncClient, config: dict[str, Any], tokens: dict[str, Any], access: str
) -> str | None:
    """Whose permissions the credential carries: the `email` claim of an ID token, else Atlassian's `/me`."""
    token = tokens.get("id_token")
    if isinstance(token, str) and token.count(".") == 2:
        import json

        try:
            claims = json.loads(
                base64.urlsafe_b64decode(token.split(".")[1] + "=" * (-len(token.split(".")[1]) % 4))
            )
            if isinstance(claims, dict) and claims.get("email"):
                return str(claims["email"])
        except (ValueError, UnicodeDecodeError):
            pass
    if (urlsplit(str(config.get("token_url"))).hostname or "").endswith("atlassian.com") and access:
        try:
            me = await client.get(ATLASSIAN_ME, headers={"Authorization": f"Bearer {access}"})
            if me.status_code == 200:
                body = me.json()
                return str(body.get("email") or body.get("name") or "") or None
        except (httpx.HTTPError, ValueError):
            return None
    return None


@router.delete(
    "/orgs/{org}/remote-credentials/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="revokeRemoteCredential",
)
async def revoke_remote_credential(org: str, name: Name, session: Db, principal: Me) -> Response:
    """Revokes the credential: its sealed values are deleted, no run can obtain a token with it."""
    _manage(principal, org)
    organisation = await _organisation(session, org)
    row = await _credential(session, organisation, name)
    store = get_secret_store()
    for what in ("refresh_token", "token", "client_secret"):
        await store.delete(session, organisation.id, secret_name(name, what))
    row.status, row.secret_ref = "revoked", None
    await record(
        session,
        principal,
        "remote_credential.revoke",
        org_id=organisation.id,
        target_type="remote_credential",
        target_id=row.id,
        name=name,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ───────────────────────────────────── discovery ─────────────────────────────────────


def card_summary(card: AgentCard) -> RemoteCardSummary:
    return RemoteCardSummary(
        name=card.name,
        description=card.description,
        endpoint=card.url,
        host=card.host,
        protocol_version=card.protocol_version,
        digest=card.digest,
        skills=[RemoteSkillInfo(id=s.id, name=s.name, description=s.description) for s in card.skills],
        auth_types=list(card.auth_types),
        scopes=list(card.scopes),
        streaming=card.streaming,
        dcr_available=card.dcr_url is not None,
    )


@router.post(
    "/orgs/{org}/agents/discover",
    response_model=RemoteAgentDiscovery,
    operation_id="discoverRemoteAgent",
)
async def discover_remote_agent(
    org: str, body: RemoteAgentDiscover, session: Db, principal: Me, allow_egress: bool = False
) -> RemoteAgentDiscovery:
    """Reads a card and suggests the registry entry; creates nothing. The card's host must be open
    in the organisation's egress allowlist first (S39-22): 409 `egress_required`, or `allow_egress`."""
    _manage(principal, org)
    organisation = await _organisation(session, org)
    await ensure_or_409(
        session,
        principal,
        organisation,
        remote_agent_targets(body.card_url),
        allow_egress=allow_egress,
        origin=f"remote-agent:{body.card_url}",
        reason=f"the remote agent at {body.card_url}",
    )
    card = await read_card(body.card_url)
    rows = (
        await session.execute(select(RemoteCredential).where(RemoteCredential.org_id == organisation.id))
    ).scalars()
    credentials = sorted(
        r.name
        for r in rows
        if r.status != "revoked"
        and (r.type in card.auth_types or (r.type == AUTH_NONE and AUTH_NONE in card.auth_types))
    )
    slug = "".join(c if c.isalnum() else "-" for c in (card.name or "remote-agent").lower()).strip("-")
    slug = ("agent-" + slug if not slug[:1].isalpha() else slug)[:62] or "remote-agent"
    proposed = AgentCreate(
        slug=slug if len(slug) >= 2 else f"{slug}-agent",
        kind="remote",
        display_name=card.name or "Remote agent",
        description=card.description or None,
        lifecycle="draft",
        spec=AgentSpec(
            runtime=A2aRuntime(
                card_url=body.card_url,
                skill=card.skills[0].id if len(card.skills) == 1 else None,
                credential=credentials[0] if len(credentials) == 1 else None,
            )
        ),
    )
    return RemoteAgentDiscovery(
        card=card_summary(card), proposed=proposed, credentials=credentials, domains=[card.host]
    )
