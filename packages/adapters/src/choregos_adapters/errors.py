# SPDX-License-Identifier: Apache-2.0
"""Adapter errors: every external failure is named and carries a `retry_after`."""

from __future__ import annotations

from choregos_core import ChoregosError
from choregos_core.hosted import UNSUPPORTED_CLUSTER


class AdapterError(ChoregosError):
    """Base of the adapter errors."""


class UpstreamError(AdapterError):
    """An external service refused or failed."""

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
    """A connector configuration that is incomplete or inconsistent."""


class UnsupportedClusterError(ConfigurationError):
    """The cluster cannot run what the run asks for: a hosted agent's native sidecar below
    Kubernetes 1.29 (S41-16). Retrying changes nothing until the cluster is upgraded."""

    code = UNSUPPORTED_CLUSTER
