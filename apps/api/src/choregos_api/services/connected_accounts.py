# SPDX-License-Identifier: Apache-2.0
"""A person's connected accounts: the `user_consent` identity mode (ADR 0135 points 1, 3, 4, 6; S63-08).

A connector declares the mode and the source's OAuth settings in its configuration, beside the modes of
S63-07:

    identity_modes: [user_consent]
    user_consent:
      authorization_endpoint: https://source.example/oauth/authorize
      token_endpoint: https://source.example/oauth/token
      revocation_endpoint: https://source.example/oauth/revoke      # optional
      client_id: choregos
      client_secret_ref: store:connector/source/oauth_client_secret  # optional (a public client has none)
      scopes: [read]

The person consents once (`begin`, `complete`: authorization code with PKCE `S256`, a signed state, the
verifier in a signed cookie bound to the state by a nonce). The refresh token is sealed (ADR 0058) at
`user/<id>/consent/<connector>`; the table row holds its name, never the token, and no route returns it.
A call draws an access token from it, used for that call only (`consented_token`). Without an account the
call fails with `account_not_connected` and the console's connect URL; there is no fallback to the
connector's own identity. Disconnecting revokes at the source when it has a revocation endpoint and deletes
the sealed secret at once.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final
from urllib.parse import urlencode, urlparse

import httpx
from choregos_adapters.http.egress import guarded
from choregos_core import secrets as core_secrets
from choregos_core import utcnow
from choregos_core.redaction import remember_secret
from choregos_core.secrets import SecretNotFound, is_store_reference
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..db.models import ConnectedAccount, Organization, OrgConnector
from ..errors import conflict, not_found, unprocessable
from ..rbac import Principal
from ..secret_store import SecretStoreUnavailable, derived_org_name, get_secret_store, store_ref
from ..security import read_session, sign_session
from .egress import egress_scope
from .identity_exchange import IdentityUnavailable

USER_CONSENT: Final = "user_consent"
#: The typed tool error of a `user_consent` need without a usable account.
NOT_CONNECTED: Final = "account_not_connected"
HANDSHAKE_COOKIE: Final = "choregos_consent"
HANDSHAKE_S: Final = 600
_STATE_PURPOSE: Final = "consent-state"
_COOKIE_PURPOSE: Final = "consent-handshake"
CALLBACK_PATH: Final = "/api/v1/me/connected-accounts/callback"
CONNECTED: Final = "connected"
RECONNECT: Final = "reconnect"

_TRANSPORT: list[httpx.AsyncBaseTransport] = []


def set_consent_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """For tests: the transport the calls to a source's authorization server go through."""
    _TRANSPORT.clear()
    if transport is not None:
        _TRANSPORT.append(transport)


def _transport(settings: Settings) -> httpx.AsyncBaseTransport | None:
    if _TRANSPORT:
        return _TRANSPORT[0]
    if settings.fakes:
        from choregos_adapters.fakes.oauth_provider import current_fake_oauth_provider

        return current_fake_oauth_provider().transport()
    return None


class AccountNotConnected(Exception):  # noqa: N818 - a named lack, rendered as a typed tool error
    """The person has no usable account at this source: the call is not made, under any identity."""

    def __init__(self, connector: str, message: str, connect_url: str) -> None:
        super().__init__(message)
        self.connector = connector
        self.connect_url = connect_url


class ConsentFailure(Exception):  # noqa: N818 - the source's refusal; `code` is its error, never a token
    def __init__(self, code: str, status: int = 0) -> None:
        super().__init__(f"the source's authorization server answered {status or 'nothing'}: {code}")
        self.code = code
        self.status = status


@dataclass(frozen=True, slots=True)
class ConsentSettings:
    authorization_endpoint: str
    token_endpoint: str
    client_id: str
    scopes: tuple[str, ...]
    revocation_endpoint: str | None = None
    client_secret_ref: str | None = None


def _acceptable_endpoint(url: str, settings: Settings) -> bool:
    """An endpoint the platform calls or sends the person's browser to: `https` (`http` only under the
    fakes), a host, no user-info, no fragment. `javascript:`, `data:` and `file:` never pass: the console
    follows `authorization_url`."""
    parsed = urlparse(url)
    scheme_ok = parsed.scheme == "https" or (parsed.scheme == "http" and settings.fakes)
    return (
        scheme_ok
        and bool(parsed.hostname)
        and not parsed.username
        and not parsed.password
        and not parsed.fragment
    )


def consent_settings(connector: Any) -> ConsentSettings | None:
    """The connector's OAuth settings, when it declares `user_consent`, they are complete and their
    endpoints acceptable (`_acceptable_endpoint`)."""
    config = getattr(connector, "config", None) or {}
    declared = config.get("identity_modes")
    if not isinstance(declared, list) or USER_CONSENT not in declared:
        return None
    raw = config.get("user_consent")
    if not isinstance(raw, dict):
        return None
    fields = {k: raw.get(k) for k in ("authorization_endpoint", "token_endpoint", "client_id")}
    if not all(isinstance(v, str) and v for v in fields.values()):
        return None
    scopes = raw.get("scopes")
    revocation = raw.get("revocation_endpoint")
    secret = raw.get("client_secret_ref")
    endpoints = [str(fields["authorization_endpoint"]), str(fields["token_endpoint"])]
    if isinstance(revocation, str) and revocation:
        endpoints.append(revocation)
    if not all(_acceptable_endpoint(url, get_settings()) for url in endpoints):
        return None
    return ConsentSettings(
        authorization_endpoint=str(fields["authorization_endpoint"]),
        token_endpoint=str(fields["token_endpoint"]),
        client_id=str(fields["client_id"]),
        scopes=tuple(str(s) for s in scopes) if isinstance(scopes, list) else (),
        revocation_endpoint=str(revocation) if isinstance(revocation, str) and revocation else None,
        client_secret_ref=str(secret) if isinstance(secret, str) and secret else None,
    )


def provider_digest(connector: Any, declared: ConsentSettings) -> str:
    """What a person consented TO: the authorization server, the client and the source's address. The
    refresh token is sent to these, and the access token to the source: when an editor of the connector
    changes one, the account is not used (and nothing is sent to the new address) until the person
    connects again."""
    config = getattr(connector, "config", None) or {}
    canonical = json.dumps(
        [
            declared.authorization_endpoint,
            declared.token_endpoint,
            declared.revocation_endpoint or "",
            declared.client_id,
            str(config.get("url") or ""),
        ]
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def supports_consent(connector: Any) -> bool:
    return consent_settings(connector) is not None


def refresh_secret_name(user_id: str, connector: str) -> str:
    return f"user/{user_id}/consent/{connector}"


def connect_url(org_slug: str, connector: str, settings: Settings | None = None) -> str:
    """The console's page that starts the consent: what `account_not_connected` hands the person."""
    base = (settings or get_settings()).public_url.rstrip("/")
    return f"{base}/me/connected-accounts/{connector}/connect?{urlencode({'org': org_slug})}"


def redirect_uri(settings: Settings | None = None) -> str:
    return (settings or get_settings()).api_url.rstrip("/") + CALLBACK_PATH


def own_secret_reference(connector: str) -> str:
    """The one `store:` reference a connector's OAuth client secret may carry: the one the catalogue install
    derives. Any other would read another person's token or another connector's key and send it, as the
    client secret, to the token endpoint the connector's editor names."""
    return store_ref(derived_org_name(connector, "oauth_client_secret"))


async def _client_secret(
    session: AsyncSession, org_id: str, settings: ConsentSettings, connector: str
) -> str:
    reference = settings.client_secret_ref
    if not reference:
        return ""
    if is_store_reference(reference) and reference != own_secret_reference(connector):
        raise IdentityUnavailable("the source's OAuth client secret reference is not this connector's own")
    try:
        if is_store_reference(reference):
            name = reference.split(":", 1)[1]
            value = (await get_secret_store().get_many(session, org_id, [name]))[name]
        else:
            value = core_secrets.resolve(reference)
    except (SecretNotFound, SecretStoreUnavailable, core_secrets.InvalidReference) as failure:
        raise IdentityUnavailable("the source's OAuth client secret cannot be read") from failure
    remember_secret(value)
    return value


async def _post(url: str, form: dict[str, str], client_secret: str, *, check: bool = True) -> dict[str, Any]:
    data = dict(form)
    if client_secret:
        data["client_secret"] = client_secret
    try:
        async with httpx.AsyncClient(timeout=10, transport=guarded(_transport(get_settings()))) as http:
            response = await http.post(url, data=data, headers={"Accept": "application/json"})
    except httpx.HTTPError as failure:
        raise ConsentFailure("unreachable") from failure
    try:
        body = response.json()
    except ValueError:
        body = {}
    body = body if isinstance(body, dict) else {}
    if check and (response.status_code >= 400 or not body.get("access_token")):
        raise ConsentFailure(str(body.get("error") or "invalid_response")[:80], response.status_code)
    return body


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


async def connector_of(session: AsyncSession, org_id: str, name: str) -> OrgConnector:
    found = (
        await session.execute(
            select(OrgConnector).where(OrgConnector.org_id == org_id, OrgConnector.name == name)
        )
    ).scalar_one_or_none()
    if found is None:
        raise not_found("Connector", name)
    return found


async def _member_org(session: AsyncSession, principal: Principal, org: str) -> Organization:
    from .organisations import organisation_by_slug

    if org not in principal.org_roles:
        raise not_found("Organisation", org)
    return await organisation_by_slug(session, org)


async def begin(
    session: AsyncSession, principal: Principal, org: str, connector_name: str
) -> tuple[str, str]:
    """`(authorization URL, handshake cookie)` for the person to consent at the source."""
    organisation = await _member_org(session, principal, org)
    connector = await connector_of(session, organisation.id, connector_name)
    declared = consent_settings(connector)
    if declared is None:
        raise unprocessable(f"`{connector_name}` does not declare the `user_consent` identity")
    settings = get_settings()
    nonce = secrets.token_urlsafe(24)
    verifier, challenge = _pkce()
    expiry = int(time.time()) + HANDSHAKE_S
    state = sign_session(
        {"u": principal.user_id, "o": organisation.id, "c": connector_name, "n": nonce, "exp": expiry},
        settings,
        purpose=_STATE_PURPOSE,
    )
    cookie = sign_session({"n": nonce, "v": verifier, "exp": expiry}, settings, purpose=_COOKIE_PURPOSE)
    query = {
        "response_type": "code",
        "client_id": declared.client_id,
        "redirect_uri": redirect_uri(settings),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if declared.scopes:
        query["scope"] = " ".join(declared.scopes)
    separator = "&" if "?" in declared.authorization_endpoint else "?"
    return f"{declared.authorization_endpoint}{separator}{urlencode(query)}", cookie


def _subject_of(body: dict[str, Any]) -> str | None:
    """The person's `sub` at the source, from the ID token when it sends one (not verified: it comes
    straight from the token endpoint over TLS, and is journaled, never trusted for access)."""
    token = body.get("id_token")
    if not isinstance(token, str) or token.count(".") != 2:
        return None
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (ValueError, UnicodeDecodeError):
        return None
    sub = claims.get("sub") if isinstance(claims, dict) else None
    return str(sub)[:320] if sub else None


async def complete(
    session: AsyncSession, principal: Principal, *, code: str, state: str, cookie: str
) -> ConnectedAccount:
    """The callback: checks the state and the handshake, exchanges the code, seals the refresh token."""
    settings = get_settings()
    claims = read_session(state, settings, purpose=_STATE_PURPOSE)
    handshake = read_session(cookie, settings, purpose=_COOKIE_PURPOSE) if cookie else None
    if claims is None or handshake is None or claims.get("n") != handshake.get("n"):
        raise unprocessable("the consent handshake is invalid or expired: start again")
    if claims.get("u") != principal.user_id:
        raise unprocessable("this consent was started by someone else")
    organisation = (await session.get(Organization, str(claims["o"]))) or None
    if organisation is None or organisation.slug not in principal.org_roles:
        raise not_found("Organisation", str(claims["o"]))
    name = str(claims["c"])
    connector = await connector_of(session, organisation.id, name)
    declared = consent_settings(connector)
    if declared is None:
        raise unprocessable(f"`{name}` does not declare the `user_consent` identity")
    try:
        secret = await _client_secret(session, organisation.id, declared, name)
    except IdentityUnavailable as unavailable:
        raise unprocessable(str(unavailable)) from unavailable
    try:
        # The callback runs under the organisation's egress allowlist like any call to a source.
        async with egress_scope(session, organisation, "api", f"oauth:{name}"):
            body = await _post(
                declared.token_endpoint,
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri(settings),
                    "client_id": declared.client_id,
                    "code_verifier": str(handshake["v"]),
                },
                secret,
            )
    except ConsentFailure as refused:
        raise unprocessable(f"the source refused the authorization code ({refused.code})") from refused
    refresh = body.get("refresh_token")
    if not isinstance(refresh, str) or not refresh:
        raise unprocessable("the source sent no refresh token: ask for an offline scope")
    store = get_secret_store()
    if not store.available():
        raise conflict("no secrets key is configured: a refresh token cannot be sealed")
    secret_name = refresh_secret_name(principal.user_id, name)
    await store.put(session, organisation.id, secret_name, refresh, principal.user_id)
    granted = str(body.get("scope") or "").split() or list(declared.scopes)
    row = await _account(session, organisation.id, principal.user_id, name)
    if row is None:
        row = ConnectedAccount(org_id=organisation.id, user_id=principal.user_id, connector=name)
        session.add(row)
    row.secret_ref = store_ref(secret_name)
    row.mode = USER_CONSENT
    row.provider_digest = provider_digest(connector, declared)
    row.scopes = granted
    row.subject = _subject_of(body)
    row.status = CONNECTED
    row.expires_at = (
        utcnow() + timedelta(seconds=int(body.get("expires_in") or 0)) if body.get("expires_in") else None
    )
    await session.flush()
    return row


async def _account(
    session: AsyncSession, org_id: str, user_id: str, connector: str
) -> ConnectedAccount | None:
    return (
        await session.execute(
            select(ConnectedAccount).where(
                ConnectedAccount.org_id == org_id,
                ConnectedAccount.user_id == user_id,
                ConnectedAccount.connector == connector,
            )
        )
    ).scalar_one_or_none()


async def accounts_of(session: AsyncSession, principal: Principal) -> list[tuple[str, ConnectedAccount]]:
    """`(organisation slug, account)` of the caller's own accounts."""
    rows = (
        await session.execute(
            select(ConnectedAccount, Organization.slug)
            .join(Organization, Organization.id == ConnectedAccount.org_id)
            .where(ConnectedAccount.user_id == principal.user_id)
            .order_by(Organization.slug, ConnectedAccount.connector)
        )
    ).all()
    return [(slug, account) for account, slug in rows]


async def disconnect(session: AsyncSession, principal: Principal, org: str, connector_name: str) -> None:
    """Revokes at the source when it offers it, deletes the sealed secret and the row, at once."""
    organisation = await _member_org(session, principal, org)
    row = await _account(session, organisation.id, principal.user_id, connector_name)
    if row is None:
        raise not_found("Connected account", connector_name)
    name = refresh_secret_name(principal.user_id, connector_name)
    store = get_secret_store()
    refresh: str | None
    try:
        refresh = (await store.get_many(session, organisation.id, [name])).get(name)
    except (SecretNotFound, SecretStoreUnavailable):
        refresh = None
    connector = await session.scalar(
        select(OrgConnector).where(
            OrgConnector.org_id == organisation.id, OrgConnector.name == connector_name
        )
    )
    declared = consent_settings(connector) if connector is not None else None
    # Local deletion first and unconditional: the source being down never keeps a token alive here.
    await store.delete(session, organisation.id, name)
    await session.delete(row)
    await session.flush()
    # Only to the authorization server the person consented to: an editor who moved it would otherwise
    # receive the refresh token as the thing to revoke.
    unchanged = (
        connector is not None
        and declared is not None
        and row.provider_digest == provider_digest(connector, declared)
    )
    if refresh and declared is not None and declared.revocation_endpoint and unchanged:
        try:
            secret = await _client_secret(session, organisation.id, declared, connector_name)
            async with egress_scope(session, organisation, "api", f"oauth:{connector_name}"):
                await _post(
                    declared.revocation_endpoint,
                    {"token": refresh, "token_type_hint": "refresh_token", "client_id": declared.client_id},
                    secret,
                    check=False,
                )
        except (ConsentFailure, IdentityUnavailable):
            pass  # best effort, RFC 7009: the secret is already gone from here


async def consented_token(session: AsyncSession, connector: OrgConnector, user_id: str, org_id: str) -> str:
    """An access token of the person at this source, for ONE call. `AccountNotConnected` or
    `IdentityUnavailable` say why not; neither is ever answered by another identity."""
    org = await session.get(Organization, org_id)
    url = connect_url(org.slug if org is not None else "", connector.name)
    declared = consent_settings(connector)
    if declared is None:
        raise IdentityUnavailable(f"`{connector.name}` does not declare the `user_consent` identity")
    row = await _account(session, org_id, user_id, connector.name) if user_id else None
    if row is None or row.status != CONNECTED:
        raise AccountNotConnected(
            connector.name,
            f"you have not connected your `{connector.name}` account: open {url}, then ask again. "
            "Continue without this source until then.",
            url,
        )
    if row.provider_digest != provider_digest(connector, declared):
        raise AccountNotConnected(
            connector.name,
            f"the settings of `{connector.name}` changed since you connected: reconnect at {url}",
            url,
        )
    name = refresh_secret_name(user_id, connector.name)
    sealed = await _sealed(session, org_id, name)
    if not sealed:
        raise AccountNotConnected(
            connector.name, f"your `{connector.name}` account is not connected: open {url}", url
        )
    secret = await _client_secret(session, org_id, declared, connector.name)
    used = sealed
    for attempt in (1, 2):
        try:
            body = await _post(
                declared.token_endpoint,
                {"grant_type": "refresh_token", "refresh_token": used, "client_id": declared.client_id},
                secret,
            )
            break
        except ConsentFailure as refused:
            if refused.code != "invalid_grant":
                raise IdentityUnavailable(
                    "the source's authorization server did not renew the person's token"
                ) from refused
            # A concurrent call may have rotated the token while this one waited on the source: the refusal
            # then concerns a token already replaced, not the person's consent. Read what is sealed NOW.
            latest = await _sealed(session, org_id, name)
            if attempt == 1 and latest and latest != used:
                used = latest
                continue
            if latest == used:
                row.status = RECONNECT
                await get_secret_store().delete(session, org_id, name)
                await session.flush()
            raise AccountNotConnected(
                connector.name,
                f"the source no longer honours your `{connector.name}` consent: reconnect at {url}",
                url,
            ) from refused
    # The person may have disconnected while the source answered: the row is read again under a lock, and a
    # rotated token is never sealed for an account that is gone (it would outlive its revocation).
    still = (
        await session.execute(
            select(ConnectedAccount)
            .where(ConnectedAccount.id == row.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if still is None or still.status != CONNECTED:
        raise AccountNotConnected(
            connector.name, f"your `{connector.name}` account is not connected: open {url}", url
        )
    rotated = body.get("refresh_token")
    if isinstance(rotated, str) and rotated and rotated != used:
        await get_secret_store().put(session, org_id, name, rotated, user_id)
    still.last_used_at = utcnow()
    expires = body.get("expires_in")
    still.expires_at = utcnow() + timedelta(seconds=int(expires)) if expires else None
    await session.flush()
    access = str(body["access_token"])
    remember_secret(access)
    return access


async def _sealed(session: AsyncSession, org_id: str, name: str) -> str | None:
    try:
        return (await get_secret_store().get_many(session, org_id, [name])).get(name)
    except (SecretNotFound, SecretStoreUnavailable):
        return None
