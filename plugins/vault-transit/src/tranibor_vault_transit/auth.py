# SPDX-License-Identifier: Apache-2.0
"""How the extension proves itself to Vault: a token, or the pod's service account (Kubernetes auth).

An auth object owns one Vault client token. `token()` returns a valid one (logging in again when the
one held is near its end), `invalidate()` forgets it after a 403 so the next call logs in again. The
login goes through the same `httpx.AsyncClient` as the transit calls, handed in by the client.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import httpx
from tranibor_core.redaction import remember_secret

DEFAULT_JWT_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
#: A login is renewed when less than this share of the lease is left.
RENEW_BELOW = 0.2


async def _read(path: str) -> str:
    return await asyncio.to_thread(Path(path).read_text, encoding="utf-8")


class AuthError(RuntimeError):
    """Vault refused the login, or the credential cannot be read. Never carries a token."""


class VaultAuth(Protocol):
    async def token(self, http: httpx.AsyncClient) -> str: ...

    def invalidate(self) -> None: ...


class TokenAuth:
    """A Vault token, given or read from a file (a Vault Agent sink rewrites it: a 403 re-reads it)."""

    def __init__(self, token: str | None = None, token_file: str | None = None) -> None:
        if bool(token) == bool(token_file):
            raise AuthError("token auth takes exactly one of a token and a token file")
        self._given = token
        self._file = token_file
        self._read: str | None = None

    async def token(self, http: httpx.AsyncClient) -> str:
        if self._given:
            value = self._given
        else:
            if self._read is None:
                try:
                    self._read = (await _read(str(self._file))).strip()
                except OSError as error:
                    raise AuthError(f"cannot read the Vault token file: {error.strerror}") from None
            value = self._read
        if not value:
            raise AuthError("the Vault token is empty")
        remember_secret(value)
        return value

    def invalidate(self) -> None:
        self._read = None


class KubernetesAuth:
    """Vault's Kubernetes auth method: the pod's service-account JWT for a role, a lease in return."""

    def __init__(
        self,
        role: str,
        mount: str = "kubernetes",
        jwt_path: str = DEFAULT_JWT_PATH,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not role:
            raise AuthError("Kubernetes auth needs a Vault role")
        self._role, self._mount, self._jwt_path, self._clock = role, mount.strip("/"), jwt_path, clock
        self._token: str | None = None
        self._expires = 0.0
        self._renew_at = 0.0
        self._lock = asyncio.Lock()

    def invalidate(self) -> None:
        self._token = None

    async def token(self, http: httpx.AsyncClient) -> str:
        async with self._lock:
            if self._token is None or self._clock() >= self._renew_at:
                await self._login(http)
            assert self._token is not None
            return self._token

    async def _login(self, http: httpx.AsyncClient) -> None:
        try:
            jwt = (await _read(self._jwt_path)).strip()
        except OSError as error:
            raise AuthError(f"cannot read the service-account token: {error.strerror}") from None
        try:
            response = await http.post(f"/v1/auth/{self._mount}/login", json={"role": self._role, "jwt": jwt})
        except httpx.HTTPError as error:
            raise AuthError(f"Vault login failed: {type(error).__name__}") from None
        if response.status_code != 200:
            raise AuthError(
                f"Vault refused the Kubernetes login for role `{self._role}` ({response.status_code})"
            )
        auth: dict[str, Any] = (response.json() or {}).get("auth") or {}
        token = auth.get("client_token")
        if not isinstance(token, str) or not token:
            raise AuthError("Vault's login answer carries no client token")
        lease = float(auth.get("lease_duration") or 0)
        now = self._clock()
        self._token = token
        self._expires = now + lease
        # A lease of 0 means "does not expire": the token is held until a 403 invalidates it.
        self._renew_at = now + lease * (1 - RENEW_BELOW) if lease > 0 else float("inf")
        remember_secret(token)
