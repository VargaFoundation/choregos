# SPDX-License-Identifier: Apache-2.0
"""The `choregos.plugins` entry point: installs the Vault transit wrapper when the deployment asks.

Configuration is the environment (an extension's `register()` takes no argument). Unset
`TRANIBOR_VAULT_ADDR`, and nothing happens: the core's keyring stays the wrapper. Set, and an invalid
setting stops the process (the extension loader's rule: a declared extension must serve).

| Variable | Meaning |
| :-- | :-- |
| `TRANIBOR_VAULT_ADDR` | `https://vault.example:8200`; enables the extension |
| `TRANIBOR_VAULT_TRANSIT_KEY` | the transit key's name, at most 24 characters |
| `TRANIBOR_VAULT_TRANSIT_MOUNT` | the engine's mount, default `transit` |
| `TRANIBOR_VAULT_NAMESPACE` | Vault Enterprise namespace, optional |
| `TRANIBOR_VAULT_AUTH` | `kubernetes` (default) or `token` |
| `TRANIBOR_VAULT_K8S_ROLE`, `_K8S_MOUNT`, `_K8S_JWT_PATH` | Kubernetes auth: role, mount, token file |
| `TRANIBOR_VAULT_TOKEN`, `TRANIBOR_VAULT_TOKEN_FILE` | token auth: one of the two |
| `TRANIBOR_VAULT_CACERT` | a CA bundle for Vault's certificate |
| `TRANIBOR_VAULT_BIND_CONTEXT` | `true` when the key was created with `derived=true` |

`TRANIBOR_SECRETS_KEKS`, when also set, is kept as the `legacy` wrapper: rows sealed before the move
still open, and `choregos admin secrets rewrap` moves them to Vault.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from .auth import DEFAULT_JWT_PATH, KubernetesAuth, TokenAuth, VaultAuth
from .client import VaultTransitClient
from .wrapper import VaultTransitKeyWrapper, VaultTransitSecretStore

NAME = "vault-transit"
_TRUE = {"1", "true", "yes"}


def _auth(env: Mapping[str, str]) -> VaultAuth:
    method = env.get("TRANIBOR_VAULT_AUTH", "kubernetes")
    if method == "token":
        return TokenAuth(
            env.get("TRANIBOR_VAULT_TOKEN") or None, env.get("TRANIBOR_VAULT_TOKEN_FILE") or None
        )
    if method == "kubernetes":
        return KubernetesAuth(
            env.get("TRANIBOR_VAULT_K8S_ROLE", ""),
            env.get("TRANIBOR_VAULT_K8S_MOUNT", "kubernetes"),
            env.get("TRANIBOR_VAULT_K8S_JWT_PATH", DEFAULT_JWT_PATH),
        )
    raise ValueError(f"TRANIBOR_VAULT_AUTH: `kubernetes` or `token`, not `{method}`")


def build_wrapper(env: Mapping[str, str]) -> VaultTransitKeyWrapper | None:
    """The wrapper the environment describes, `None` when `TRANIBOR_VAULT_ADDR` is unset."""
    address = env.get("TRANIBOR_VAULT_ADDR", "").strip()
    if not address:
        return None
    key = env.get("TRANIBOR_VAULT_TRANSIT_KEY", "").strip()
    if not key:
        raise ValueError("TRANIBOR_VAULT_TRANSIT_KEY is required with TRANIBOR_VAULT_ADDR")
    from tranibor_api.secret_store import EnvKeyWrapper, parse_keyring

    keyring = parse_keyring(env.get("TRANIBOR_SECRETS_KEKS"))
    client = VaultTransitClient(
        address,
        _auth(env),
        key,
        mount=env.get("TRANIBOR_VAULT_TRANSIT_MOUNT", "transit"),
        namespace=env.get("TRANIBOR_VAULT_NAMESPACE") or None,
        ca_cert=env.get("TRANIBOR_VAULT_CACERT") or None,
    )
    return VaultTransitKeyWrapper(
        client,
        bind_context=env.get("TRANIBOR_VAULT_BIND_CONTEXT", "").lower() in _TRUE,
        legacy=EnvKeyWrapper(keyring) if keyring is not None else None,
    )


def register() -> None:
    """Idempotent: the API loads the extensions more than once."""
    from tranibor_api.secret_store import set_secret_store

    wrapper = build_wrapper(os.environ)
    if wrapper is not None:
        set_secret_store(VaultTransitSecretStore(wrapper))
