# SPDX-License-Identifier: Apache-2.0
"""An organisation's outbound proxy, as the platform's clients see it (ADR 0087).

The setting lives in the API (a sealed password, `Organization.settings`); this module is the lowest
layer that every client shares: the resolved `ProxyConfig`, the percent-encoded URL a client dials,
and the context in which a call made for one organisation finds ITS proxy for a kind of traffic.

The context mirrors `choregos_core.egress`: the API enters it next to the egress scope, a guarded
client reads it on each request. Nothing here reads the environment: httpx ignores `HTTP(S)_PROXY`
when a transport is given, and the setting is explicit and per organisation.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from urllib.parse import quote

from .errors import ChoregosError

KINDS: tuple[str, ...] = ("web_fetch", "web_search", "knowledge_web", "search_engines")
SCHEMES: tuple[str, ...] = ("http", "https")


class OutboundProxyError(ChoregosError):
    """The proxy could not carry the call. Names the proxy (endpoint, never the password); the
    caller never retries the call directly."""


@dataclass(frozen=True, slots=True)
class ProxyConfig:
    endpoint: str
    scheme: str = "http"
    username: str = ""
    password: str = field(default="", repr=False)

    @property
    def label(self) -> str:
        """What an error or a log line says: the endpoint, never a credential."""
        return f"{self.scheme}://{self.endpoint}"

    @property
    def url(self) -> str:
        """`scheme://user:pass@host:port`, user and password percent-encoded: a provider's password
        may hold `=`, `@` or `/`, and left raw it is a 407 (or another host)."""
        if not self.username and not self.password:
            return self.label
        user = quote(self.username, safe="")
        secret = quote(self.password, safe="")
        return f"{self.scheme}://{user}:{secret}@{self.endpoint}"

    def secrets(self) -> tuple[str, ...]:
        """Every form of the password a message could carry (raw, encoded)."""
        forms = {self.password, quote(self.password, safe="")}
        return tuple(form for form in forms if form)


_PROXIES: ContextVar[Mapping[str, ProxyConfig] | None] = ContextVar("choregos_outbound_proxies", default=None)


def current_proxy(kind: str) -> ProxyConfig | None:
    """The proxy the calling organisation's `kind` of traffic goes through, or `None` (direct)."""
    proxies = _PROXIES.get()
    return proxies.get(kind) if proxies else None


def push_proxies(proxies: Mapping[str, ProxyConfig]) -> Token[Mapping[str, ProxyConfig] | None]:
    """`use_proxies` for a block too long to indent: pair it with `pop_proxies` in a `finally`."""
    return _PROXIES.set(dict(proxies))


def pop_proxies(token: Token[Mapping[str, ProxyConfig] | None]) -> None:
    _PROXIES.reset(token)


@contextmanager
def use_proxies(proxies: Mapping[str, ProxyConfig]) -> Iterator[None]:
    token = push_proxies(proxies)
    try:
        yield
    finally:
        pop_proxies(token)


__all__ = [
    "KINDS",
    "SCHEMES",
    "OutboundProxyError",
    "ProxyConfig",
    "current_proxy",
    "pop_proxies",
    "push_proxies",
    "use_proxies",
]
