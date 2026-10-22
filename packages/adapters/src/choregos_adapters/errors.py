# SPDX-License-Identifier: Apache-2.0
"""Erreurs des adaptateurs : toute panne externe est nommée et porte un `retry_after`."""

from __future__ import annotations

from choregos_core import ChoregosError
from choregos_core.hosted import UNSUPPORTED_CLUSTER


class AdapterError(ChoregosError):
    """Base des erreurs d'adaptateur."""


class UpstreamError(AdapterError):
    """Un service externe a refusé ou échoué."""

    def __init__(
        self,
        service: str,
        message: str,
        *,
        retry_after: int | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(f"[{service}] {message}")
        self.service = service
        self.retry_after = retry_after
        self.status_code = status_code


class ConfigurationError(AdapterError):
    """Configuration de connecteur incomplète ou incohérente."""


class UnsupportedClusterError(ConfigurationError):
    """The cluster cannot run what the run asks for: a hosted agent's native sidecar below
    Kubernetes 1.29 (S41-16). Retrying changes nothing until the cluster is upgraded."""

    code = UNSUPPORTED_CLUSTER
