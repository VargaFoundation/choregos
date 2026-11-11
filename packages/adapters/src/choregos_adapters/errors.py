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


class HeadMovedError(UpstreamError):
    """The pull request's head is not the commit the merge named: nothing was merged (ADR 0152).

    `expected` is the commit the caller named, `actual` the head the forge holds when it could be
    read. The same merge would be refused the same way: the caller judges the new head, it does not
    retry."""

    def __init__(self, service: str, *, expected: str, actual: str | None = None) -> None:
        where = f"is at {actual}" if actual else "has moved"
        super().__init__(
            service,
            f"the pull request's head {where}, not at {expected}: nothing was merged",
            status_code=409,
        )
        self.expected = expected
        self.actual = actual


class ConfigurationError(AdapterError):
    """A connector configuration that is incomplete or inconsistent."""


class UnsupportedClusterError(ConfigurationError):
    """The cluster cannot run what the run asks for: a hosted agent's native sidecar below
    Kubernetes 1.29 (S41-16). Retrying changes nothing until the cluster is upgraded."""

    code = UNSUPPORTED_CLUSTER
