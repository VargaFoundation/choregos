# SPDX-License-Identifier: Apache-2.0
"""The outbound proxy as an httpx transport (ADR 0087).

`httpx` honours `HTTP(S)_PROXY` only when no `transport` is given, and every guarded client gives
one: an environment variable would be skipped silently. So the proxy is explicit, and these
transports carry it:

- `FixedProxyTransport(proxy)`: every request goes through that proxy (the `test` action,
  `web_fetch`, a search backend given its proxy by the API);
- `ProxyRoutingTransport(kind, direct)`: reads the calling organisation's proxy for `kind` of traffic
  from `choregos_core.outbound_proxy` on EACH request, so a client built outside the scope still
  finds it; with none, the request goes `direct`.

Fail closed: with a proxy, a request that the proxy cannot carry (unreachable, refused `CONNECT`,
407) is an `OutboundProxyHttpError` naming the proxy; it is never sent again without it. The
target is a name: for an `https` target httpx sends `CONNECT <name>:<port>` and the proxy resolves.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
from choregos_core.outbound_proxy import OutboundProxyError, ProxyConfig, current_proxy

from ..errors import AdapterError

TransportFactory = Callable[[ProxyConfig], httpx.AsyncBaseTransport]


class OutboundProxyHttpError(OutboundProxyError, AdapterError, httpx.ConnectError):
    """A call the proxy could not carry. An `AdapterError` and an `httpx.ConnectError`, so callers'
    `except httpx.HTTPError` report it like an unreachable host, with a message that names the proxy."""

    def __init__(self, proxy: ProxyConfig, host: str, reason: str) -> None:
        OutboundProxyError.__init__(
            self, f"the outbound proxy {proxy.label} could not carry `{host}`: {reason}"
        )
        self.proxy_label = proxy.label
        # `httpx.RequestError.__init__` is skipped on purpose: `request` stays unset.
        self._request = None


def _default_factory(proxy: ProxyConfig) -> httpx.AsyncBaseTransport:
    return httpx.AsyncHTTPTransport(proxy=httpx.Proxy(proxy.url))


_FACTORY: list[TransportFactory] = []


def set_proxy_transport_factory(factory: TransportFactory | None) -> None:
    """Replaces how a proxy's transport is built (the tests: a `MockTransport`, no network)."""
    _FACTORY.clear()
    if factory is not None:
        _FACTORY.append(factory)


def make_proxy_transport(proxy: ProxyConfig) -> httpx.AsyncBaseTransport:
    return (_FACTORY[0] if _FACTORY else _default_factory)(proxy)


async def carry(
    proxy: ProxyConfig, transport: httpx.AsyncBaseTransport, request: httpx.Request
) -> httpx.Response:
    """One request through `transport` (a proxy's): a failure is the proxy's, named, never retried."""
    host = request.url.host
    try:
        response = await transport.handle_async_request(request)
    except httpx.TransportError as bad:
        # The class only: an httpx message can carry the URL, and the URL carries credentials.
        raise OutboundProxyHttpError(proxy, host, type(bad).__name__) from None
    if response.status_code == 407:
        await response.aclose()
        raise OutboundProxyHttpError(proxy, host, "the proxy refused the credentials (407)")
    return response


class FixedProxyTransport(httpx.AsyncBaseTransport):
    def __init__(self, proxy: ProxyConfig) -> None:
        self._proxy = proxy
        self._inner = make_proxy_transport(proxy)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await carry(self._proxy, self._inner, request)

    async def aclose(self) -> None:
        await self._inner.aclose()


class ProxyRoutingTransport(httpx.AsyncBaseTransport):
    def __init__(self, kind: str, direct: httpx.AsyncBaseTransport | None = None) -> None:
        self._kind = kind
        self._direct = direct or httpx.AsyncHTTPTransport()
        self._via: dict[str, httpx.AsyncBaseTransport] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        proxy = current_proxy(self._kind)
        if proxy is None:
            return await self._direct.handle_async_request(request)
        inner = self._via.get(proxy.url)
        if inner is None:
            inner = self._via[proxy.url] = make_proxy_transport(proxy)
        return await carry(proxy, inner, request)

    async def aclose(self) -> None:
        await self._direct.aclose()
        for inner in self._via.values():
            await inner.aclose()


__all__ = [
    "FixedProxyTransport",
    "OutboundProxyHttpError",
    "ProxyRoutingTransport",
    "carry",
    "make_proxy_transport",
    "set_proxy_transport_factory",
]
