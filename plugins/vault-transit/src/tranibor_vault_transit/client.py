# SPDX-License-Identifier: Apache-2.0
"""Async HTTP to Vault's transit engine: encrypt, decrypt, rewrap, and the key's latest version.

The `httpx.AsyncClient` is built here (or handed in a `transport` for the tests: `FakeVault`). A 403
logs in again once; a connection error or a 5xx is tried once more. A refusal carries Vault's own
`errors` text, which names no secret, and never the token.
"""

from __future__ import annotations

import base64
import re
from typing import Any

import httpx

from .auth import AuthError, VaultAuth

_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MOUNT = re.compile(r"^[A-Za-z0-9_/-]{1,64}$")


class VaultError(RuntimeError):
    """Vault answered with a refusal, or could not be reached."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class VaultTransitClient:
    def __init__(
        self,
        address: str,
        auth: VaultAuth,
        key: str,
        *,
        mount: str = "transit",
        namespace: str | None = None,
        ca_cert: str | None = None,
        timeout_s: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not _NAME.match(key):
            raise ValueError(f"invalid Vault transit key name: {key!r}")
        if not _MOUNT.match(mount):
            raise ValueError(f"invalid Vault transit mount: {mount!r}")
        if not address.startswith(("http://", "https://")):
            raise ValueError("the Vault address must start with http:// or https://")
        self.key = key
        self._mount = mount.strip("/")
        self._auth = auth
        headers = {"X-Vault-Namespace": namespace} if namespace else {}
        self._http = httpx.AsyncClient(
            base_url=address.rstrip("/"),
            headers=headers,
            timeout=timeout_s,
            transport=transport,
            verify=ca_cert if ca_cert and transport is None else True,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        relogged = retried = False
        while True:
            try:
                token = await self._auth.token(self._http)
            except AuthError as error:
                raise VaultError(str(error)) from None
            try:
                response = await self._http.request(
                    method, f"/v1/{self._mount}/{path}", json=body, headers={"X-Vault-Token": token}
                )
            except httpx.HTTPError as error:
                if not retried:
                    retried = True
                    continue
                raise VaultError(f"Vault is unreachable ({type(error).__name__})") from None
            if response.status_code == 403 and not relogged:
                relogged = True
                self._auth.invalidate()
                continue
            if response.status_code >= 500 and not retried:
                retried = True
                continue
            if response.status_code >= 400:
                raise VaultError(_message(response), response.status_code)
            try:
                data = (response.json() or {}).get("data")
            except (ValueError, AttributeError):
                data = None
            if not isinstance(data, dict):
                raise VaultError("Vault's answer carries no data", response.status_code)
            return data

    async def encrypt(self, plaintext: bytes, context: bytes | None = None) -> str:
        """The ciphertext `vault:v<N>:...` of `plaintext` under the key's latest version."""
        return await self._cipher("encrypt", "plaintext", plaintext, context)

    async def rewrap(self, ciphertext: str, context: bytes | None = None) -> str:
        """The same plaintext under the latest version; the plaintext never leaves Vault."""
        body: dict[str, Any] = {"ciphertext": ciphertext}
        _add_context(body, context)
        data = await self._call("POST", f"rewrap/{self.key}", body)
        return _text(data, "ciphertext")

    async def decrypt(self, ciphertext: str, context: bytes | None = None) -> bytes:
        body: dict[str, Any] = {"ciphertext": ciphertext}
        _add_context(body, context)
        data = await self._call("POST", f"decrypt/{self.key}", body)
        try:
            return base64.b64decode(_text(data, "plaintext"), validate=True)
        except ValueError:
            raise VaultError("Vault's `plaintext` is not base64") from None

    async def latest_version(self) -> int:
        data = await self._call("GET", f"keys/{self.key}")
        version = data.get("latest_version")
        if not isinstance(version, int) or version < 1:
            raise VaultError(f"Vault reports no version for the transit key `{self.key}`")
        return version

    async def _cipher(self, operation: str, field: str, payload: bytes, context: bytes | None) -> str:
        body: dict[str, Any] = {field: base64.b64encode(payload).decode()}
        _add_context(body, context)
        return _text(await self._call("POST", f"{operation}/{self.key}", body), "ciphertext")


def _add_context(body: dict[str, Any], context: bytes | None) -> None:
    if context is not None:
        body["context"] = base64.b64encode(context).decode()


def _text(data: dict[str, Any], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value:
        raise VaultError(f"Vault's answer carries no `{field}`")
    return value


def _message(response: httpx.Response) -> str:
    try:
        errors = (response.json() or {}).get("errors") or []
    except ValueError:
        errors = []
    detail = "; ".join(str(e) for e in errors)[:300]
    return f"Vault refused the call ({response.status_code})" + (f": {detail}" if detail else "")
