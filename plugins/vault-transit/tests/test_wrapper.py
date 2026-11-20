# SPDX-License-Identifier: Apache-2.0
"""The wrapper against the fake Vault: wrap, unwrap, binding, rotation, failures, auth.

The fake really encrypts (AES-GCM per key version), so a tampered or moved wrapped key fails for the
reason Vault would fail it. None of this ran against a real Vault.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from tranibor_api.secret_store import EnvKeyWrapper, SecretStoreUnavailable, parse_keyring
from tranibor_vault_transit import (
    KubernetesAuth,
    TokenAuth,
    VaultTransitClient,
    VaultTransitKeyWrapper,
)
from tranibor_vault_transit.auth import AuthError
from tranibor_vault_transit.fake import FakeVault

from .conftest import SA_JWT

AAD = b"org|connector/a/b|1"


def test_wrap_then_unwrap_roundtrips_and_the_kek_id_names_the_version(
    wrapper: VaultTransitKeyWrapper,
) -> None:
    dek = os.urandom(32)
    wrapped, nonce, kek_id = wrapper.wrap(dek, AAD)
    assert kek_id == "choregos-v1" == wrapper.active_id
    assert len(kek_id) <= 32
    assert wrapped.startswith(b"vault:v1:") and dek not in wrapped
    assert wrapper.unwrap(wrapped, nonce, AAD, kek_id) == dek


def test_a_rotation_changes_the_active_id_and_old_rows_still_open(
    vault: FakeVault, client: VaultTransitClient
) -> None:
    now = [0.0]
    wrapper = VaultTransitKeyWrapper(client, refresh_s=30, clock=lambda: now[0])
    dek = os.urandom(32)
    wrapped, nonce, old = wrapper.wrap(dek, AAD)
    assert vault.rotate("choregos") == 2
    assert wrapper.active_id == "choregos-v1", "the id is cached for refresh_s"
    now[0] = 31.0
    assert wrapper.active_id == "choregos-v2"
    assert wrapper.unwrap(wrapped, nonce, AAD, old) == dek
    assert wrapper.wrap(dek, AAD)[2] == "choregos-v2"
    wrapper.close()


def test_a_wrapped_key_moved_to_another_row_does_not_open_when_bound(vault: FakeVault) -> None:
    vault.derived = True
    bound = VaultTransitKeyWrapper(
        VaultTransitClient("https://v", TokenAuth(token="hvs.static"), "choregos", transport=vault.transport),
        bind_context=True,
    )
    wrapped, nonce, kek_id = bound.wrap(os.urandom(32), AAD)
    with pytest.raises(SecretStoreUnavailable, match="does not open"):
        bound.unwrap(wrapped, nonce, b"another|row|1", kek_id)
    bound.close()


def test_a_tampered_wrapped_key_does_not_open(wrapper: VaultTransitKeyWrapper) -> None:
    wrapped, nonce, kek_id = wrapper.wrap(os.urandom(32), AAD)
    tampered = wrapped[:-4] + (b"AAAA" if not wrapped.endswith(b"AAAA") else b"BBBB")
    with pytest.raises(SecretStoreUnavailable, match="does not open"):
        wrapper.unwrap(tampered, nonce, AAD, kek_id)


def test_a_key_of_another_wrapper_is_refused_by_name(wrapper: VaultTransitKeyWrapper) -> None:
    with pytest.raises(SecretStoreUnavailable, match="sealed under the key `k1`"):
        wrapper.unwrap(b"x", b"n", AAD, "k1")


def test_a_row_of_the_old_keyring_opens_through_the_legacy_wrapper(
    vault: FakeVault, client: VaultTransitClient
) -> None:
    import base64

    ring = parse_keyring("k1:" + base64.b64encode(os.urandom(32)).decode())
    assert ring is not None
    legacy = EnvKeyWrapper(ring)
    dek = os.urandom(32)
    old = legacy.wrap(dek, AAD)
    with_legacy = VaultTransitKeyWrapper(client, legacy=legacy)
    assert with_legacy.unwrap(old[0], old[1], AAD, old[2]) == dek
    with_legacy.close()


def test_a_down_vault_is_an_unavailable_store_not_a_crash(
    vault: FakeVault, wrapper: VaultTransitKeyWrapper
) -> None:
    vault.fail_next = [503, 503]
    with pytest.raises(SecretStoreUnavailable, match="Vault transit: Vault refused the call \\(503\\)"):
        wrapper.wrap(os.urandom(32), AAD)


def test_one_transient_5xx_is_retried(vault: FakeVault, wrapper: VaultTransitKeyWrapper) -> None:
    vault.fail_next = [502]
    assert wrapper.wrap(os.urandom(32), AAD)[2] == "choregos-v1"


def test_a_missing_key_is_named(vault: FakeVault, client: VaultTransitClient) -> None:
    other = VaultTransitClient("https://v", TokenAuth(token="hvs.static"), "nokey", transport=vault.transport)
    wrapper = VaultTransitKeyWrapper(other)
    with pytest.raises(SecretStoreUnavailable, match="encryption key not found"):
        wrapper.wrap(os.urandom(32), AAD)
    wrapper.close()


def test_a_key_name_that_cannot_fit_kek_id_is_refused(vault: FakeVault) -> None:
    long = VaultTransitClient("https://v", TokenAuth(token="t"), "k" * 25, transport=vault.transport)
    with pytest.raises(ValueError, match="at most 24"):
        VaultTransitKeyWrapper(long)


# ───────────────────────────── auth ─────────────────────────────


def test_a_bad_token_is_refused_after_one_retry(vault: FakeVault) -> None:
    wrapper = VaultTransitKeyWrapper(
        VaultTransitClient("https://v", TokenAuth(token="hvs.wrong"), "choregos", transport=vault.transport)
    )
    with pytest.raises(SecretStoreUnavailable, match="permission denied"):
        wrapper.wrap(os.urandom(32), AAD)
    assert len(vault.calls) == 2
    wrapper.close()


def test_token_auth_takes_exactly_one_source() -> None:
    with pytest.raises(AuthError):
        TokenAuth()
    with pytest.raises(AuthError):
        TokenAuth("a", "/b")


def test_a_token_file_is_read_again_after_a_403(vault: FakeVault, tmp_path: pytest.TempPathFactory) -> None:
    path = tmp_path / "sink"  # type: ignore[operator]
    path.write_text("hvs.old", encoding="utf-8")
    wrapper = VaultTransitKeyWrapper(
        VaultTransitClient(
            "https://v", TokenAuth(token_file=str(path)), "choregos", transport=vault.transport
        )
    )
    path.write_text("hvs.static", encoding="utf-8")  # the agent rewrote the sink before the call
    assert wrapper.wrap(os.urandom(32), AAD)[2] == "choregos-v1"
    wrapper.close()


@pytest.fixture
def k8s_wrapper(k8s_client: VaultTransitClient) -> Iterator[VaultTransitKeyWrapper]:
    made = VaultTransitKeyWrapper(k8s_client)
    yield made
    made.close()


def test_kubernetes_auth_logs_in_once_and_sends_the_namespace(
    k8s_vault: FakeVault, k8s_wrapper: VaultTransitKeyWrapper
) -> None:
    for _ in range(3):
        dek = os.urandom(32)
        wrapped, nonce, kek = k8s_wrapper.wrap(dek, AAD)
        assert k8s_wrapper.unwrap(wrapped, nonce, AAD, kek) == dek
    assert k8s_vault.logins == 1
    assert set(k8s_vault.namespaces) == {"team-a"}


def test_kubernetes_auth_logs_in_again_when_the_lease_runs_low(
    k8s_vault: FakeVault, k8s_wrapper: VaultTransitKeyWrapper
) -> None:
    k8s_wrapper.wrap(os.urandom(32), AAD)
    k8s_vault.now += k8s_vault.lease_s * 0.9
    k8s_wrapper.wrap(os.urandom(32), AAD)
    assert k8s_vault.logins == 2


def test_kubernetes_auth_logs_in_again_after_a_revoked_token(
    k8s_vault: FakeVault, k8s_wrapper: VaultTransitKeyWrapper
) -> None:
    k8s_wrapper.wrap(os.urandom(32), AAD)
    k8s_vault.tokens.clear()  # an operator revoked it
    k8s_wrapper.wrap(os.urandom(32), AAD)
    assert k8s_vault.logins == 2


def test_a_role_vault_does_not_know_is_refused_without_leaking_the_jwt(
    vault: FakeVault, jwt_file: str
) -> None:
    auth = KubernetesAuth("unknown-role", jwt_path=jwt_file, clock=vault.clock)
    wrapper = VaultTransitKeyWrapper(
        VaultTransitClient("https://v", auth, "choregos", transport=vault.transport)
    )
    with pytest.raises(SecretStoreUnavailable, match="refused the Kubernetes login for role") as refused:
        wrapper.wrap(os.urandom(32), AAD)
    assert SA_JWT not in str(refused.value)
    wrapper.close()


def test_an_unreadable_service_account_token_is_a_named_refusal(vault: FakeVault) -> None:
    auth = KubernetesAuth("choregos", jwt_path="/nonexistent/token", clock=vault.clock)
    wrapper = VaultTransitKeyWrapper(
        VaultTransitClient("https://v", auth, "choregos", transport=vault.transport)
    )
    with pytest.raises(SecretStoreUnavailable, match="cannot read the service-account token"):
        wrapper.wrap(os.urandom(32), AAD)
    wrapper.close()
