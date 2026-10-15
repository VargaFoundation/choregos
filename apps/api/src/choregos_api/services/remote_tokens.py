# SPDX-License-Identifier: Apache-2.0
"""The access token a remote agent's run presents (ADR 0062).

Only the platform holds the long-lived part. For OAuth, the REFRESH token lives sealed in the secret
store (ADR 0058) and is rewritten at every refresh (providers like Atlassian rotate it); the
orchestrator exchanges it for a short-lived ACCESS token and hands only that one to the run's Secret.
The runner never sees the refresh token, and nothing here is written to a payload or a log.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
from choregos_core import utcnow
from choregos_core.redaction import remember_secret
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import RemoteCredential
from ..secret_store import SecretStore, SecretStoreUnavailable, get_secret_store
from .remote_agents import a2a_transport

#: A refresh answer that carries no `expires_in` is taken to last this long.
DEFAULT_LIFETIME_S = 3000


class RemoteCredentialError(RuntimeError):
    """The credential cannot give an access token: revoked, never authorised, refused by the provider."""


def secret_name(credential: str, what: str) -> str:
    return f"remote/{credential}/{what}"


@dataclass(frozen=True)
class AccessToken:
    token: str
    expires_at: Any = None


async def store_refresh_token(
    session: AsyncSession, store: SecretStore, row: RemoteCredential, token: str, actor: str | None
) -> None:
    name = secret_name(row.name, "refresh_token")
    try:
        await store.put(session, row.org_id, name, token, actor)
    except SecretStoreUnavailable as exc:
        raise RemoteCredentialError(str(exc)) from exc
    row.secret_ref = f"store:{name}"


async def _read(session: AsyncSession, store: SecretStore, org_id: str, name: str) -> str:
    try:
        return (await store.get_many(session, org_id, [name]))[name]
    except Exception as exc:  # the store says what is missing; the caller needs one error type
        raise RemoteCredentialError(f"the sealed value `{name}` cannot be read: {exc}") from exc


async def access_token_for(
    session: AsyncSession, org_id: str, name: str, *, store: SecretStore | None = None
) -> AccessToken:
    """A usable access token for the named credential; rewrites the rotated refresh token.

    The credential row is locked for the exchange: two runs refreshing at once would present the same
    rotating refresh token twice, and the provider would refuse the second.
    """
    store = store or get_secret_store()
    row: RemoteCredential | None = (
        await session.execute(
            select(RemoteCredential)
            .where(RemoteCredential.org_id == org_id, RemoteCredential.name == name)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise RemoteCredentialError(f"no remote credential `{name}`")
    if row.status == "revoked":
        raise RemoteCredentialError(f"the remote credential `{name}` is revoked")
    if row.type == "none":
        return AccessToken("")
    if row.type == "bearer":
        token = await _read(session, store, org_id, secret_name(name, "token"))
        remember_secret(token)
        return AccessToken(token)
    if row.status == "pending" or not row.secret_ref:
        raise RemoteCredentialError(f"the remote credential `{name}` was never authorised")
    return await _refresh(session, store, row)


async def _refresh(session: AsyncSession, store: SecretStore, row: RemoteCredential) -> AccessToken:
    config = row.config or {}
    refresh = await _read(session, store, row.org_id, secret_name(row.name, "refresh_token"))
    remember_secret(refresh)
    form = {"grant_type": "refresh_token", "refresh_token": refresh, "client_id": config.get("client_id", "")}
    client_secret_name = config.get("client_secret_name")
    if client_secret_name:
        form["client_secret"] = await _read(session, store, row.org_id, client_secret_name)
    token_url = str(config.get("token_url") or "")
    if urlsplit(token_url).scheme != "https":
        raise RemoteCredentialError(f"the token endpoint of `{row.name}` is not https")
    try:
        async with httpx.AsyncClient(transport=a2a_transport(), timeout=20, follow_redirects=False) as client:
            response = await client.post(token_url, data=form)
    except httpx.HTTPError as exc:
        raise RemoteCredentialError(f"the token endpoint is unreachable ({type(exc).__name__})") from exc
    if response.status_code >= 400:
        row.status = "error"
        row.last_error = f"the provider refused the refresh (HTTP {response.status_code}): reconnect"
        raise RemoteCredentialError(row.last_error)
    body = response.json()
    access = body.get("access_token")
    if not access:
        raise RemoteCredentialError("the token endpoint answered without an access token")
    remember_secret(access)
    rotated = body.get("refresh_token")
    if rotated and rotated != refresh:
        remember_secret(rotated)
        await store_refresh_token(session, store, row, rotated, "remote-token-refresh")
    lifetime = int(body.get("expires_in") or DEFAULT_LIFETIME_S)
    row.last_refresh_at = utcnow()
    row.expires_at = utcnow() + timedelta(seconds=lifetime)
    row.status, row.last_error = "connected", None
    return AccessToken(str(access), row.expires_at)
