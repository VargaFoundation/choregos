# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from tranibor_vault_transit import KubernetesAuth, TokenAuth, VaultTransitClient, VaultTransitKeyWrapper
from tranibor_vault_transit.fake import FakeVault

SA_JWT = "sa-jwt-of-the-api-pod"


@pytest.fixture
def vault() -> FakeVault:
    fake = FakeVault()
    fake.create_key("choregos")
    fake.allow_token("hvs.static")
    return fake


@pytest.fixture
def jwt_file(tmp_path: Path) -> str:
    path = tmp_path / "token"
    path.write_text(SA_JWT + "\n", encoding="utf-8")
    return str(path)


@pytest.fixture
def k8s_vault(vault: FakeVault) -> FakeVault:
    vault.k8s_roles["choregos"] = {SA_JWT}
    return vault


@pytest.fixture
def client(vault: FakeVault) -> VaultTransitClient:
    return VaultTransitClient(
        "https://vault.test:8200", TokenAuth(token="hvs.static"), "choregos", transport=vault.transport
    )


@pytest.fixture
def wrapper(client: VaultTransitClient) -> Iterator[VaultTransitKeyWrapper]:
    made = VaultTransitKeyWrapper(client)
    yield made
    made.close()


@pytest.fixture
def k8s_client(k8s_vault: FakeVault, jwt_file: str) -> VaultTransitClient:
    auth = KubernetesAuth("choregos", jwt_path=jwt_file, clock=k8s_vault.clock)
    return VaultTransitClient(
        "https://vault.test:8200", auth, "choregos", namespace="team-a", transport=k8s_vault.transport
    )
