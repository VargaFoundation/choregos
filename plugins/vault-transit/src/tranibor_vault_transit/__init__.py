# SPDX-License-Identifier: Apache-2.0
"""The HashiCorp Vault Transit key wrapper for the connector secrets (ADR 0103).

An extension: the core ships no Vault client (ADR 0103 §5). This distribution brings one, behind the
`KeyWrapper` seam, and installs it with `set_secret_store` when the deployment configures it.
"""

from __future__ import annotations

from .auth import KubernetesAuth, TokenAuth, VaultAuth
from .client import VaultError, VaultTransitClient
from .wrapper import VaultTransitKeyWrapper, VaultTransitSecretStore

__all__ = [
    "KubernetesAuth",
    "TokenAuth",
    "VaultAuth",
    "VaultError",
    "VaultTransitClient",
    "VaultTransitKeyWrapper",
    "VaultTransitSecretStore",
]
