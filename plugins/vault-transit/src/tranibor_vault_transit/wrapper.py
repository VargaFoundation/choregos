# SPDX-License-Identifier: Apache-2.0
"""The `KeyWrapper` of ADR 0103 over Vault's transit engine, and the store that rotates through rewrap.

- `wrap` encrypts the 32-byte data key with the transit key; Vault's ciphertext `vault:v<N>:...` is the
  wrapped key, its version is the row's `kek_id` (`<key>-v<N>`, at most 32 characters). The nonce column
  holds nothing (Vault keeps it inside the ciphertext).
- `unwrap` decrypts it. A row whose `kek_id` is not this key's (sealed under the environment keyring
  before the move) opens through `legacy`, when one is given: that is the migration path.
- With `bind_context`, the row's additional data (`dek|org|name|version`) is sent as the transit
  `context`, so Vault itself refuses a wrapped key moved to another row. It needs a key created with
  `derived=true`; without it the same binding still holds in the AES-GCM layer above (the core's `seal`).
- Rotation: `vault write -f transit/keys/<key>/rotate` makes the wrapper's `active_id` the new version
  (within `refresh_s`); `VaultTransitSecretStore.rewrap` then moves the rows with Vault's `rewrap`, so
  the data keys never leave Vault during a rotation.

**The protocol is synchronous** (ADR 0103): `wrap` and `unwrap` run the async client on a private event
loop in a daemon thread and wait for it, so the HTTP is async but the caller's thread is blocked for the
call. `put` and `get_many` therefore hold the API's event loop for one Vault round trip per secret.
`rewrap`, the one bulk operation, is async end to end.
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
from collections.abc import Callable, Coroutine, Sequence
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tranibor_api.db.models import OrgSecret
from tranibor_api.secret_store import KeyWrapper, SealedDbSecretStore, SecretStoreUnavailable

from .client import VaultError, VaultTransitClient

T = TypeVar("T")

#: `org_secrets.kek_id` is 32 characters; `-v` and the version take the rest.
MAX_KEY_NAME = 24
_KEK_ID = re.compile(r"^(?P<key>[A-Za-z0-9_-]+)-v(?P<version>[0-9]+)$")
_CIPHERTEXT = re.compile(r"^vault:v(?P<version>[0-9]+):")
CALL_TIMEOUT_S = 30.0


class _Loop:
    """A private event loop in a daemon thread: the Vault client lives and is used there only."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def _started(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                threading.Thread(target=loop.run_forever, name="vault-transit", daemon=True).start()
                self._loop = loop
            return self._loop

    def run(self, coroutine: Coroutine[Any, Any, T]) -> T:
        return asyncio.run_coroutine_threadsafe(coroutine, self._started()).result(CALL_TIMEOUT_S)

    async def run_async(self, coroutine: Coroutine[Any, Any, T]) -> T:
        """From another event loop: awaits without blocking it."""
        future = asyncio.run_coroutine_threadsafe(coroutine, self._started())
        return await asyncio.wait_for(asyncio.wrap_future(future), CALL_TIMEOUT_S)

    def close(self) -> None:
        with self._lock:
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._loop.stop)
                self._loop = None


def kek_id_of(key: str, version: int) -> str:
    kek_id = f"{key}-v{version}"
    if len(kek_id) > 32:
        raise SecretStoreUnavailable(f"the key id `{kek_id}` does not fit org_secrets.kek_id (32 characters)")
    return kek_id


class VaultTransitKeyWrapper:
    def __init__(
        self,
        client: VaultTransitClient,
        *,
        bind_context: bool = False,
        legacy: KeyWrapper | None = None,
        refresh_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if len(client.key) > MAX_KEY_NAME:
            raise ValueError(f"the transit key name is at most {MAX_KEY_NAME} characters (kek_id is 32)")
        self._client = client
        self._bind = bind_context
        self._legacy = legacy
        self._refresh_s = refresh_s
        self._clock = clock
        self._loop = _Loop()
        self._active: str | None = None
        self._checked = 0.0

    # ───────────────────────────── the protocol ─────────────────────────────

    @property
    def active_id(self) -> str:
        if self._active is None or self._clock() - self._checked >= self._refresh_s:
            self._loop.run(self.arefresh())
        assert self._active is not None
        return self._active

    @active_id.setter
    def active_id(self, value: str) -> None:
        """Pins the id (tests, or a migration); the next refresh reads Vault again."""
        self._active, self._checked = value, self._clock()

    def wrap(self, dek: bytes, aad: bytes) -> tuple[bytes, bytes, str]:
        return self._loop.run(self.awrap(dek, aad))

    def unwrap(self, wrapped: bytes, nonce: bytes, aad: bytes, kek_id: str) -> bytes:
        return self._loop.run(self.aunwrap(wrapped, nonce, aad, kek_id))

    def close(self) -> None:
        self._loop.run(self._client.aclose())
        self._loop.close()

    async def on_loop(self, coroutine: Coroutine[Any, Any, T]) -> T:
        """Runs one of the `a...` coroutines below on the wrapper's own loop, awaiting from the caller's
        without blocking it: the HTTP client belongs to that loop and to no other."""
        return await self._loop.run_async(coroutine)

    # ───────────────────────────── the async core ─────────────────────────────

    def _context(self, aad: bytes) -> bytes | None:
        return b"dek|" + aad if self._bind else None

    async def arefresh(self) -> str:
        try:
            version = await self._client.latest_version()
        except VaultError as error:
            raise SecretStoreUnavailable(f"Vault transit: {error}") from None
        self._active, self._checked = kek_id_of(self._client.key, version), self._clock()
        return self._active

    async def awrap(self, dek: bytes, aad: bytes) -> tuple[bytes, bytes, str]:
        try:
            ciphertext = await self._client.encrypt(dek, self._context(aad))
        except VaultError as error:
            raise SecretStoreUnavailable(f"Vault transit: {error}") from None
        kek_id = kek_id_of(self._client.key, _version(ciphertext))
        self._active, self._checked = kek_id, self._clock()
        return ciphertext.encode(), b"", kek_id

    async def aunwrap(self, wrapped: bytes, nonce: bytes, aad: bytes, kek_id: str) -> bytes:
        if not self.is_ours(kek_id):
            if self._legacy is not None:
                return self._legacy.unwrap(wrapped, nonce, aad, kek_id)
            raise SecretStoreUnavailable(
                f"the secret is sealed under the key `{kek_id}`, which is not the Vault transit key "
                f"`{self._client.key}`"
            )
        try:
            return await self._client.decrypt(wrapped.decode(), self._context(aad))
        except VaultError as error:
            if error.status == 400:
                raise SecretStoreUnavailable("the sealed key does not open (wrong key, or moved)") from None
            raise SecretStoreUnavailable(f"Vault transit: {error}") from None

    async def arewrap(self, wrapped: bytes, aad: bytes) -> tuple[bytes, str]:
        """The wrapped key under the latest version, decrypted by Vault only: `(wrapped, kek_id)`."""
        try:
            ciphertext = await self._client.rewrap(wrapped.decode(), self._context(aad))
        except VaultError as error:
            if error.status == 400:
                raise SecretStoreUnavailable("the sealed key does not open (wrong key, or moved)") from None
            raise SecretStoreUnavailable(f"Vault transit: {error}") from None
        return ciphertext.encode(), kek_id_of(self._client.key, _version(ciphertext))

    def is_ours(self, kek_id: str) -> bool:
        match = _KEK_ID.match(kek_id)
        return match is not None and match["key"] == self._client.key

    @property
    def legacy(self) -> KeyWrapper | None:
        return self._legacy


def _version(ciphertext: str) -> int:
    match = _CIPHERTEXT.match(ciphertext)
    if match is None:
        raise SecretStoreUnavailable("Vault returned a ciphertext in an unknown format")
    return int(match["version"])


class VaultTransitSecretStore(SealedDbSecretStore):
    """The sealed database store with a Vault wrapper; its `rewrap` rotates through Vault's rewrap."""

    def __init__(self, wrapper: VaultTransitKeyWrapper) -> None:
        super().__init__(wrapper=wrapper)
        self._vault = wrapper

    async def rewrap(self, session: AsyncSession, org_id: str | None = None) -> int:
        """Moves the rows not under the active version onto it. A row of the transit key goes through
        Vault's `rewrap` (the data key is never in this process); a row of the legacy keyring is opened
        by it and sealed by Vault. The values are never touched. Returns the rows changed."""
        active = await self._vault.on_loop(self._vault.arefresh())
        query = select(OrgSecret).where(OrgSecret.kek_id != active)
        if org_id is not None:
            query = query.where(OrgSecret.org_id == org_id)
        rows: Sequence[OrgSecret] = list((await session.execute(query)).scalars())
        for row in rows:
            aad = f"{row.org_id}|{row.name}|{row.version}".encode()
            if self._vault.is_ours(row.kek_id):
                row.wrapped_dek, row.kek_id = await self._vault.on_loop(
                    self._vault.arewrap(row.wrapped_dek, aad)
                )
            else:
                dek = await self._vault.on_loop(
                    self._vault.aunwrap(row.wrapped_dek, row.dek_nonce, aad, row.kek_id)
                )
                row.wrapped_dek, row.dek_nonce, row.kek_id = await self._vault.on_loop(
                    self._vault.awrap(dek, aad)
                )
        await session.flush()
        return len(rows)
