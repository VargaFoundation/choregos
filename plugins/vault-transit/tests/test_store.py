# SPDX-License-Identifier: Apache-2.0
"""The wrapper under the core's real `SealedDbSecretStore`, on a SQLite `org_secrets`, and the rotation."""

from __future__ import annotations

import base64
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import Connection, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from tranibor_api.db.models import OrgSecret
from tranibor_api.secret_store import SealedDbSecretStore, parse_keyring
from tranibor_core.secrets import SecretNotFound
from tranibor_vault_transit import (
    VaultTransitClient,
    VaultTransitKeyWrapper,
    VaultTransitSecretStore,
)
from tranibor_vault_transit.fake import FakeVault

ORG = "org-1"
NAME = "connector/jira/api_token"


def _create_table(connection: Connection) -> None:
    OrgSecret.__table__.create(connection)  # type: ignore[attr-defined]


@pytest.fixture
async def sessions(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/s.db")
    async with engine.begin() as connection:
        await connection.run_sync(_create_table)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def store(wrapper: VaultTransitKeyWrapper) -> VaultTransitSecretStore:
    return VaultTransitSecretStore(wrapper)


async def test_a_secret_is_sealed_under_vault_and_opens(
    sessions: async_sessionmaker[AsyncSession], store: VaultTransitSecretStore, vault: FakeVault
) -> None:
    async with sessions() as session:
        await store.put(session, ORG, NAME, "s3cr3t-value", "alice")
        await session.commit()
    async with sessions() as session:
        assert await store.get_many(session, ORG, [NAME]) == {NAME: "s3cr3t-value"}
        row = (await session.execute(select(OrgSecret))).scalar_one()
        assert row.kek_id == "choregos-v1" and row.wrapped_dek.startswith(b"vault:v1:")
        assert b"s3cr3t-value" not in row.ciphertext
    assert [c[0] for c in vault.calls].count("POST") >= 2


async def test_rotation_rewraps_through_vault_without_the_data_key_leaving_it(
    sessions: async_sessionmaker[AsyncSession], store: VaultTransitSecretStore, vault: FakeVault
) -> None:
    async with sessions() as session:
        await store.put(session, ORG, NAME, "keep-me", None)
        await session.commit()
    vault.rotate("choregos")
    async with sessions() as session:
        assert await store.rewrap(session) == 1
        await session.commit()
    async with sessions() as session:
        assert await store.rewrap(session) == 0, "idempotent"
        row = (await session.execute(select(OrgSecret))).scalar_one()
        assert row.kek_id == "choregos-v2" and row.wrapped_dek.startswith(b"vault:v2:")
        assert await store.get_many(session, ORG, [NAME]) == {NAME: "keep-me"}
    assert any(c[1].startswith("rewrap/") for c in vault.calls)
    # Once the old version is retired, the rotated row still opens and an unrotated one would not.
    vault.min_decryption_version["choregos"] = 2
    async with sessions() as session:
        assert await store.get_many(session, ORG, [NAME]) == {NAME: "keep-me"}


async def test_a_retired_version_is_a_missing_secret_not_a_crash(
    sessions: async_sessionmaker[AsyncSession], store: VaultTransitSecretStore, vault: FakeVault
) -> None:
    async with sessions() as session:
        await store.put(session, ORG, NAME, "v", None)
        await session.commit()
    vault.rotate("choregos")
    vault.min_decryption_version["choregos"] = 2
    async with sessions() as session:
        with pytest.raises(SecretNotFound, match="does not open"):
            await store.get_many(session, ORG, [NAME])


async def test_rows_sealed_by_the_env_keyring_migrate_to_vault(
    sessions: async_sessionmaker[AsyncSession], client: VaultTransitClient
) -> None:
    ring = parse_keyring("k1:" + base64.b64encode(os.urandom(32)).decode())
    assert ring is not None
    async with sessions() as session:
        await SealedDbSecretStore(keyring=ring).put(session, ORG, NAME, "from-env", None)
        await session.commit()
    from tranibor_api.secret_store import EnvKeyWrapper

    migrating = VaultTransitKeyWrapper(client, legacy=EnvKeyWrapper(ring))
    store = VaultTransitSecretStore(migrating)
    async with sessions() as session:
        assert await store.get_many(session, ORG, [NAME]) == {NAME: "from-env"}, "opens before the move"
        assert await store.rewrap(session) == 1
        await session.commit()
    async with sessions() as session:
        row = (await session.execute(select(OrgSecret))).scalar_one()
        assert row.kek_id == "choregos-v1"
        # Vault alone now opens it: no legacy keyring.
        alone = VaultTransitSecretStore(VaultTransitKeyWrapper(client))
        assert await alone.get_many(session, ORG, [NAME]) == {NAME: "from-env"}
    migrating.close()


async def test_the_store_is_available_without_the_env_key(
    monkeypatch: pytest.MonkeyPatch, store: VaultTransitSecretStore
) -> None:
    monkeypatch.delenv("TRANIBOR_SECRETS_KEKS", raising=False)
    assert store.available() and store.key_wrapper() is not None
