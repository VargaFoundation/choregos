# SPDX-License-Identifier: Apache-2.0
"""A tool call under the person's identity: RFC 8693 token exchange (ADR 0135, S63-07).

A connector declares the identity modes it supports in its configuration:

    identity_modes: [service, user_exchange]      # absent: [service], as before
    token_exchange: {audience: iris-mcp}          # the audience of the exchanged token

`user_exchange` swaps, at each call, the person's token from the identity provider for a token whose
audience is the connector's; the connector then receives THAT token as its bearer. The exchanged
token is used for one call, never stored, never logged, never handed to a run or a pod (ADR 0107
point 7). There is no fallback: without a valid subject token, `IdentityUnavailable`, and the broker
makes no call under the connector's own identity.

Where the subject token comes from:
1. the bearer the MCP door validated for this person, in this request (`door_bearer`);
2. a refresh token of the provider sealed per person at sign-in (`seal_refresh_token`), from which a
   fresh access token is drawn here, server-side.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Any, Final, Literal

import httpx
from choregos_adapters.errors import AdapterError
from choregos_adapters.token_exchange import ExchangedToken, TokenExchangeClient, claims_of
from choregos_core.redaction import remember_secret
from choregos_core.secrets import SecretNotFound
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..secret_store import get_secret_store

IdentityMode = Literal["service", "user_exchange", "user_consent"]
SERVICE: Final = "service"
USER_EXCHANGE: Final = "user_exchange"
USER_CONSENT: Final = "user_consent"
MODES: Final = (SERVICE, USER_EXCHANGE, USER_CONSENT)
#: The typed tool error of a call in a person's mode that cannot go out under it.
UNAVAILABLE: Final = "identity_unavailable"

#: (user id, bearer): set by the MCP door once it validated the bearer for that person. Read only for
#: that same person, so a bearer never serves another one.
_DOOR: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar("door_bearer", default=None)
_TRANSPORT: list[httpx.AsyncBaseTransport] = []


class IdentityUnavailable(Exception):  # noqa: N818 - a named lack, rendered as a typed tool error
    """The call cannot go out in the person's mode: it is refused, never made under another identity."""


def door_bearer(user_id: str, bearer: str) -> None:
    """The MCP door validated `bearer` as `user_id`'s: a call in this request may exchange it."""
    _DOOR.set((user_id, bearer))


def set_exchange_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """For tests: the transport the exchange and refresh calls go through."""
    _TRANSPORT.clear()
    if transport is not None:
        _TRANSPORT.append(transport)


def _transport(settings: Settings) -> httpx.AsyncBaseTransport | None:
    if _TRANSPORT:
        return _TRANSPORT[0]
    if settings.fakes:
        from choregos_adapters.fakes.identity_provider import current_fake_identity_provider

        return current_fake_identity_provider().transport()
    return None


def supports_identity(connector: Any, mode: str) -> bool:
    """Whether the connector declares `mode` (`config["identity_modes"]`; `service` when it says nothing)."""
    declared = (getattr(connector, "config", None) or {}).get("identity_modes")
    modes = [str(m) for m in declared] if isinstance(declared, list) and declared else [SERVICE]
    if mode == USER_EXCHANGE:
        return USER_EXCHANGE in modes and exchange_audience(connector) is not None
    return mode in modes


def exchange_audience(connector: Any) -> str | None:
    declared = (getattr(connector, "config", None) or {}).get("token_exchange")
    audience = declared.get("audience") if isinstance(declared, dict) else None
    return str(audience) if audience else None


def refresh_secret_name(user_id: str) -> str:
    return f"user/{user_id}/oidc_refresh"


_DISCOVERED: dict[str, str] = {}


async def _discovered_token_endpoint(issuer: str) -> str:
    """The provider's token endpoint from its discovery document (cached); a failure is an unavailable
    identity, never a guess."""
    issuer = issuer.rstrip("/")
    if issuer not in _DISCOVERED:
        try:
            async with httpx.AsyncClient(timeout=5) as http:
                document = (await http.get(f"{issuer}/.well-known/openid-configuration")).json()
            endpoint = str(document.get("token_endpoint") or "")
        except (httpx.HTTPError, ValueError, AttributeError) as failure:
            raise IdentityUnavailable(
                "the identity provider's discovery document cannot be read"
            ) from failure
        if not endpoint:
            raise IdentityUnavailable("the identity provider's discovery document has no token endpoint")
        _DISCOVERED[issuer] = endpoint
    return _DISCOVERED[issuer]


async def _client(settings: Settings) -> TokenExchangeClient:
    endpoint = settings.token_exchange_endpoint or settings.oidc_token_endpoint
    if not endpoint and not settings.fakes:
        endpoint = await _discovered_token_endpoint(settings.oidc_issuer)
    from choregos_adapters.fakes.identity_provider import ENDPOINT

    return TokenExchangeClient(
        endpoint or ENDPOINT,
        client_id=settings.token_exchange_client_id or settings.oidc_client_id,
        client_secret=settings.token_exchange_client_secret or settings.oidc_client_secret,
        transport=_transport(settings),
    )


async def seal_refresh_token(session: AsyncSession, org_ids: list[str], user_id: str, refresh: str) -> None:
    """At sign-in: the provider's refresh token of the person, sealed (ADR 0058) in each organisation
    they belong to. Never read back except by `subject_token`."""
    store = get_secret_store()
    if not store.available():
        return
    for org_id in org_ids:
        await store.put(session, org_id, refresh_secret_name(user_id), refresh, user_id)


async def forget_refresh_token(session: AsyncSession, org_ids: list[str], user_id: str) -> None:
    store = get_secret_store()
    for org_id in org_ids:
        await store.delete(session, org_id, refresh_secret_name(user_id))


async def subject_token(
    session: AsyncSession,
    org_id: str,
    user_id: str,
    settings: Settings | None = None,
    *,
    secret_name: str | None = None,
) -> str:
    """The person's access token for this call, or `IdentityUnavailable`. `secret_name` names another sealed
    refresh token (a standing delegation's offline token, S63-14): the door's bearer is then not used."""
    settings = settings or get_settings()
    door = _DOOR.get()
    if secret_name is None and door is not None and door[0] == user_id:
        remember_secret(door[1])
        return door[1]
    name = secret_name or refresh_secret_name(user_id)
    try:
        sealed = (await get_secret_store().get_many(session, org_id, [name])).get(name)
    except SecretNotFound:
        sealed = None
    if not sealed:
        raise IdentityUnavailable("no token of the person is available: they must sign in again")
    try:
        drawn: ExchangedToken = await (await _client(settings)).refresh(sealed)
    except (AdapterError, httpx.HTTPError) as failure:
        raise IdentityUnavailable("the identity provider did not renew the person's token") from failure
    remember_secret(drawn.access_token)
    if drawn.refresh_token:
        remember_secret(drawn.refresh_token)
        if not await _still_sealed(session, org_id, name):
            # Signed out or revoked while the provider answered: the rotated token is not kept, and it is
            # killed at the provider, or it would outlive the revocation as an orphan credential.
            await revoke_offline_token(drawn.refresh_token)
            raise IdentityUnavailable("the person's token was withdrawn while it was renewed")
        await get_secret_store().put(session, org_id, name, drawn.refresh_token, user_id)
    return drawn.access_token


async def _still_sealed(session: AsyncSession, org_id: str, name: str) -> bool:
    try:
        return bool((await get_secret_store().get_many(session, org_id, [name])).get(name))
    except SecretNotFound:
        return False


@dataclass(frozen=True, slots=True)
class Acting:
    """Who a call is for: the person, the mode, and the actor named in the token's `act`."""

    mode: IdentityMode
    user_id: str = ""
    #: The person's subject at the provider, for the audit (`as: person:<subject>`).
    subject: str = ""
    org_id: str = ""
    actor: str | None = None
    #: A standing delegation's sealed offline token (S63-14), drawn instead of the sign-in's refresh token.
    refresh_secret: str = ""

    def audit(self, connector_name: str) -> dict[str, str]:
        """`as` and the mode, as the TOOL_CALLED event carries them (ADR 0107 point 8)."""
        if self.mode in (USER_EXCHANGE, USER_CONSENT):
            return {"as": f"person:{self.subject or self.user_id}", "identity_mode": self.mode}
        return {"as": f"assigned:{connector_name}", "identity_mode": SERVICE}


async def exchanged_token(session: AsyncSession, connector: Any, acting: Acting) -> str:
    """A token for `connector`, for the person: one call's. `IdentityUnavailable` says why not."""
    audience = exchange_audience(connector)
    if not supports_identity(connector, USER_EXCHANGE) or audience is None:
        raise IdentityUnavailable(f"`{connector.name}` does not declare the `user_exchange` identity")
    settings = get_settings()
    allowed = {a for a in settings.token_exchange_audiences if a}
    if allowed and audience not in allowed:
        # The audience comes from the connector's configuration, which its editors write: the operator's
        # list bounds what a person's token may be exchanged for.
        raise IdentityUnavailable(
            f"the audience of `{connector.name}` is not one this deployment exchanges for"
        )
    subject = await subject_token(
        session, acting.org_id, acting.user_id, settings, secret_name=acting.refresh_secret or None
    )
    try:
        issued = await (await _client(settings)).exchange(subject, audience=audience, actor=acting.actor)
    except (AdapterError, httpx.HTTPError) as failure:
        raise IdentityUnavailable(
            "the identity provider refused to issue a token for this source"
        ) from failure
    remember_secret(issued.access_token)
    claimed = claims_of(issued.access_token).get("aud")
    if claimed is not None:
        audiences = (
            [claimed] if isinstance(claimed, str) else list(claimed) if isinstance(claimed, list) else []
        )
        if audience not in audiences:
            # A token for another audience is never handed to this source (token confusion).
            raise IdentityUnavailable("the identity provider issued a token for another audience")
    return issued.access_token


def revocation_endpoint(settings: Settings) -> str:
    if settings.token_revocation_endpoint:
        return settings.token_revocation_endpoint
    if settings.fakes:
        from choregos_adapters.fakes.identity_provider import REVOCATION_ENDPOINT

        return REVOCATION_ENDPOINT
    return ""


async def issue_offline_token(session: AsyncSession, org_id: str, user_id: str) -> str:
    """An offline token of the person for a standing delegation (ADR 0137 point 3): the provider issues it,
    to the exchange client, from the person's own token (their sealed sign-in refresh token, or the door's
    bearer in this request). The caller seals it; this function never stores it."""
    settings = get_settings()
    subject = await subject_token(session, org_id, user_id, settings)
    try:
        offline = await (await _client(settings)).offline(subject)
        remember_secret(offline)
        return offline
    except (AdapterError, httpx.HTTPError) as failure:
        raise IdentityUnavailable(
            "the identity provider did not issue an offline token for this person"
        ) from failure


async def revoke_offline_token(token: str) -> bool:
    """Revoke the token at the provider when it has a revocation endpoint; False when it has none or the
    call failed (the sealed copy is deleted by the caller either way)."""
    settings = get_settings()
    endpoint = revocation_endpoint(settings)
    if not endpoint:
        return False
    try:
        return await (await _client(settings)).revoke(endpoint, token)
    except (AdapterError, httpx.HTTPError, IdentityUnavailable):
        return False
