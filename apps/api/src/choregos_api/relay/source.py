# SPDX-License-Identifier: Apache-2.0
"""Where the relay learns the organisation's proxy (ADR 0091 §3, §5): the API's internal route, cached.

The relay holds neither the database credentials nor the secrets key: it asks
`GET /internal/orgs/{org}/outbound-proxy/{kind}` with its relay JWT. Any answer other than a 200 is
`ProxySourceError`, and the relay then refuses the traffic: it does not know whether a proxy applies,
so it cannot go direct.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

import httpx
from choregos_core.outbound_proxy import KINDS, SCHEMES, ProxyConfig
from choregos_core.redaction import remember_secret

#: How long a resolved answer is reused (ADR 0091 §5): a proxy removed or disabled stops being used
#: within this delay.
CACHE_TTL_S = 30.0


class ProxySourceError(Exception):
    """The relay could not learn whether a proxy applies: it refuses, never goes direct."""


class ProxySource(Protocol):
    async def get(self, kind: str) -> ProxyConfig | None:
        """The proxy `kind` must go through, `None` for direct; `ProxySourceError` when unknown."""
        ...


class ApiProxySource:
    def __init__(
        self,
        api_url: str,
        org: str,
        token: str,
        *,
        timeout_s: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = api_url.rstrip("/")
        self._org = org
        self._token = token
        self._timeout_s = timeout_s
        self._transport = transport

    async def get(self, kind: str) -> ProxyConfig | None:
        if kind not in KINDS:
            raise ProxySourceError(f"`{kind}` is not a kind of traffic")
        url = f"{self._base}/internal/orgs/{self._org}/outbound-proxy/{kind}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s, transport=self._transport) as client:
                answer = await client.get(url, headers={"Authorization": f"Bearer {self._token}"})
        except httpx.HTTPError as bad:
            raise ProxySourceError(f"the API could not be asked ({type(bad).__name__})") from None
        if answer.status_code != 200:
            raise ProxySourceError(f"the API answered HTTP {answer.status_code}")
        try:
            found = answer.json()["proxy"]
        except (ValueError, KeyError, TypeError):
            raise ProxySourceError("the API's answer has no `proxy`") from None
        if found is None:
            return None
        try:
            config = ProxyConfig(
                str(found["endpoint"]), str(found["scheme"]), str(found["username"]), str(found["password"])
            )
        except (KeyError, TypeError):
            raise ProxySourceError("the API's proxy is incomplete") from None
        if config.scheme not in SCHEMES or ":" not in config.endpoint:
            raise ProxySourceError("the API's proxy is malformed")
        for form in config.secrets():
            remember_secret(form)
        return config


class CachedProxySource:
    """`source`'s answers reused for `ttl_s`, per kind. A failure clears the entry and is not cached: the
    next call asks again, and nothing stale is served while the API cannot be asked."""

    def __init__(
        self, source: ProxySource, *, ttl_s: float = CACHE_TTL_S, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._source = source
        self._ttl_s = ttl_s
        self._clock = clock
        self._entries: dict[str, tuple[float, ProxyConfig | None]] = {}

    async def get(self, kind: str) -> ProxyConfig | None:
        entry = self._entries.get(kind)
        now = self._clock()
        if entry is not None and now - entry[0] < self._ttl_s:
            return entry[1]
        found = await self._source.get(kind)  # a failure raises: nothing is stored, nothing stale served
        self._entries[kind] = (now, found)
        return found


__all__ = ["CACHE_TTL_S", "ApiProxySource", "CachedProxySource", "ProxySource", "ProxySourceError"]
