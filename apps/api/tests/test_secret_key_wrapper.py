# SPDX-License-Identifier: Apache-2.0
"""The key-wrapper seam (S47-26, ADR 0103): a KMS-like wrapper replaces the environment keyring."""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Any

import pytest
from choregos_api.secret_store import (
    ENV_KEKS,
    EnvKeyWrapper,
    KeyWrapper,
    SealedDbSecretStore,
    SecretStoreUnavailable,
    parse_keyring,
)
from choregos_core.secrets import SecretIntrouvable


class FakeKms:
    """A scriptable stand-in for a KMS or Vault transit: the key never leaves it."""

    active_id = "fake-kms:key-1"

    def __init__(self) -> None:
        self.calls: list[str] = []

    @staticmethod
    def _mask(aad: bytes) -> bytes:
        return hashlib.sha256(b"fake-kms|" + aad).digest()

    def wrap(self, dek: bytes, aad: bytes) -> tuple[bytes, bytes, str]:
        self.calls.append("wrap")
        return bytes(a ^ b for a, b in zip(dek, self._mask(aad), strict=True)), b"n", self.active_id

    def unwrap(self, wrapped: bytes, nonce: bytes, aad: bytes, kek_id: str) -> bytes:
        self.calls.append("unwrap")
        if kek_id != self.active_id:
            raise SecretStoreUnavailable(f"the key `{kek_id}` is not held by this KMS")
        return bytes(a ^ b for a, b in zip(wrapped, self._mask(aad), strict=True))


class _Migrating:
    """Opens with the old wrapper, seals with the new one: what an operator's migration does."""

    def __init__(self, old: KeyWrapper, new: KeyWrapper) -> None:
        self._old, self._new = old, new
        self.active_id = new.active_id

    def wrap(self, dek: bytes, aad: bytes) -> tuple[bytes, bytes, str]:
        return self._new.wrap(dek, aad)

    def unwrap(self, wrapped: bytes, nonce: bytes, aad: bytes, kek_id: str) -> bytes:
        side = self._new if kek_id == self._new.active_id else self._old
        return side.unwrap(wrapped, nonce, aad, kek_id)


def _ring(kek_id: str = "k1") -> Any:
    key = base64.b64encode(os.urandom(32)).decode()
    ring = parse_keyring(f"{kek_id}:{key}")
    assert ring is not None
    return ring


async def _org_id() -> str:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        return (await session.execute(select(Organization.id))).scalar_one()


async def test_a_kms_wrapper_seals_without_the_env_key(
    app: Any, org: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.db.models import OrgSecret
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    monkeypatch.delenv(ENV_KEKS, raising=False)
    kms = FakeKms()
    store = SealedDbSecretStore(wrapper=kms)
    assert store.available()
    org_id = await _org_id()
    async with session_scope() as session:
        await store.put(session, org_id, "connector/a/b", "kms-value", None)
    async with session_scope() as session:
        assert await store.get_many(session, org_id, ["connector/a/b"]) == {"connector/a/b": "kms-value"}
        row = (await session.execute(select(OrgSecret))).scalar_one()
        assert row.kek_id.startswith("fake-kms:")
    assert kms.calls == ["wrap", "unwrap"]


async def test_rows_sealed_by_the_env_keyring_still_open(app: Any, org: str) -> None:
    from choregos_api.db.session import session_scope

    ring = _ring()
    org_id = await _org_id()
    async with session_scope() as session:
        await SealedDbSecretStore(keyring=ring).put(session, org_id, "connector/a/b", "env-value", None)
    async with session_scope() as session:
        opened = await SealedDbSecretStore(wrapper=EnvKeyWrapper(ring)).get_many(
            session, org_id, ["connector/a/b"]
        )
    assert opened == {"connector/a/b": "env-value"}


async def test_rewrap_moves_rows_to_the_wrapper(app: Any, org: str) -> None:
    from choregos_api.db.models import OrgSecret
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    org_id = await _org_id()
    ring = _ring("k1")
    async with session_scope() as session:
        await SealedDbSecretStore(keyring=ring).put(session, org_id, "connector/a/b", "move-me", None)
    kms = FakeKms()
    migration = SealedDbSecretStore(wrapper=_Migrating(EnvKeyWrapper(ring), kms))
    async with session_scope() as session:
        assert await migration.rewrap(session) == 1
        assert await migration.rewrap(session) == 0, "idempotent"
    async with session_scope() as session:
        row = (await session.execute(select(OrgSecret))).scalar_one()
        assert row.kek_id == kms.active_id
        assert await SealedDbSecretStore(wrapper=kms).get_many(session, org_id, ["connector/a/b"]) == {
            "connector/a/b": "move-me"
        }
        with pytest.raises(SecretIntrouvable, match="no longer lists"):
            await SealedDbSecretStore(wrapper=EnvKeyWrapper(ring)).get_many(
                session, org_id, ["connector/a/b"]
            )
