# SPDX-License-Identifier: Apache-2.0
"""The entry point: inert unconfigured, installs the store when configured, refuses a bad setting."""

from __future__ import annotations

from collections.abc import Iterator
from importlib import metadata

import pytest
from tranibor_api.secret_store import SealedDbSecretStore, get_secret_store, set_secret_store
from tranibor_vault_transit.extension import build_wrapper, register
from tranibor_vault_transit.wrapper import VaultTransitSecretStore


@pytest.fixture(autouse=True)
def _restore() -> Iterator[None]:
    yield
    set_secret_store(None)


def test_the_entry_point_is_declared_under_the_group() -> None:
    points = {p.name: p.value for p in metadata.entry_points(group="choregos.plugins")}
    assert points["vault-transit"] == "tranibor_vault_transit.extension:register"


def test_unconfigured_it_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRANIBOR_VAULT_ADDR", raising=False)
    monkeypatch.delenv("TRANIBOR_FAKES", raising=False)  # the suite may set it: the default is then the fake
    register()
    assert type(get_secret_store()) is SealedDbSecretStore


def test_configured_it_installs_the_vault_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRANIBOR_VAULT_ADDR", "https://vault.test:8200")
    monkeypatch.setenv("TRANIBOR_VAULT_TRANSIT_KEY", "choregos")
    monkeypatch.setenv("TRANIBOR_VAULT_K8S_ROLE", "choregos")
    register()
    register()  # idempotent
    store = get_secret_store()
    assert isinstance(store, VaultTransitSecretStore) and store.available()


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"TRANIBOR_VAULT_ADDR": "https://v"}, "TRANSIT_KEY is required"),
        ({"TRANIBOR_VAULT_ADDR": "https://v", "TRANIBOR_VAULT_TRANSIT_KEY": "k"}, "needs a Vault role"),
        (
            {
                "TRANIBOR_VAULT_ADDR": "https://v",
                "TRANIBOR_VAULT_TRANSIT_KEY": "k",
                "TRANIBOR_VAULT_AUTH": "ldap",
            },
            "`kubernetes` or `token`",
        ),
        (
            {
                "TRANIBOR_VAULT_ADDR": "https://v",
                "TRANIBOR_VAULT_TRANSIT_KEY": "k",
                "TRANIBOR_VAULT_AUTH": "token",
            },
            "exactly one of a token",
        ),
        (
            {
                "TRANIBOR_VAULT_ADDR": "vault:8200",
                "TRANIBOR_VAULT_TRANSIT_KEY": "k",
                "TRANIBOR_VAULT_K8S_ROLE": "r",
            },
            "must start with http",
        ),
    ],
)
def test_a_bad_setting_stops_the_start(env: dict[str, str], message: str) -> None:
    with pytest.raises(Exception, match=message):
        build_wrapper(env)


def test_token_auth_from_the_environment() -> None:
    wrapper = build_wrapper(
        {
            "TRANIBOR_VAULT_ADDR": "https://v",
            "TRANIBOR_VAULT_TRANSIT_KEY": "k",
            "TRANIBOR_VAULT_AUTH": "token",
            "TRANIBOR_VAULT_TOKEN": "hvs.x",
            "TRANIBOR_VAULT_BIND_CONTEXT": "true",
        }
    )
    assert wrapper is not None
