# SPDX-License-Identifier: Apache-2.0
"""The guarded HTTP transport: the platform's outbound clients refuse a host the organisation has
not opened (S39-22b, ADR 0066).

`GuardedTransport` wraps any `httpx` transport and checks EVERY request against the egress policy
the caller put in context (`choregos_core.egress.use_policy`) — redirects included, because httpx
sends each hop through the transport. A refusal happens before the inner transport sees the
request. Without a policy in context the call fails closed in `enforce` (a forgotten call site
shows up in the tests) and passes with a log line in `warn`.
"""

from __future__ import annotations

import httpx
from choregos_core.egress import (
    EgressRefusedError,
    EgressScope,
    current_scope,
    default_mode,
    normalize_host,
    refusal_message,
)

from ..errors import AdapterError


class EgressRefusedHttpError(EgressRefusedError, AdapterError, httpx.ConnectError):
    """An `EgressRefusedError` the adapters' callers already handle: it is an `AdapterError`, and
    an `httpx.ConnectError`, so the `except httpx.HTTPError` of every client reports it like an
    unreachable host — with a message that names the cause."""

    def __init__(self, refusal: EgressRefusedError) -> None:
        EgressRefusedError.__init__(
            self, refusal.host, refusal.port, refusal.family, refusal.reason, refusal.org
        )
        # `httpx.RequestError.__init__` is skipped on purpose: `request` stays unset.
        self._request = None

    def __str__(self) -> str:
        return refusal_message(self.host, self.port, self.family, self.reason, self.org)


class GuardedTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self._inner = inner or httpx.AsyncHTTPTransport()

    def _check(self, request: httpx.Request) -> None:
        host = normalize_host(request.url.host or "")
        port = request.url.port or (443 if request.url.scheme == "https" else 80)
        scope: EgressScope | None = current_scope()
        try:
            if scope is None:
                if default_mode() == "enforce":
                    raise EgressRefusedError(host, port, "api", "no_policy_in_context")
                _warn_no_policy(host, port)
                return
            scope.policy.check(host, port, scope.family, scope.origin)
        except EgressRefusedError as refusal:
            raise EgressRefusedHttpError(refusal) from None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._check(request)
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def _warn_no_policy(host: str, port: int) -> None:
    import structlog

    structlog.get_logger("choregos.egress").warning(
        "egress.would_refuse", host=host, port=port, reason="no_policy_in_context"
    )


def guarded(transport: httpx.AsyncBaseTransport | None = None) -> GuardedTransport:
    """Wrap `transport` (the tests' `MockTransport`, or the real one when `None`)."""
    return GuardedTransport(transport)
