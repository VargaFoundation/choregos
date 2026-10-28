# SPDX-License-Identifier: Apache-2.0
"""Rate limit on the routes WITHOUT a principal: sign-in (`/api/v1/auth/*`) and webhooks
(`/api/v1/webhooks/*`).

A sliding one-minute window in memory, per client address and per replica: with N replicas a
client gets up to N x `CHOREGOS_RATE_LIMIT_PER_MINUTE`. There is no shared store, by decision.

The key is the address the nearest trusted proxy saw (`client_address`), never the left-most
`X-Forwarded-For` entry, which the client writes: rotating that header buys no new window, and a
client outside the trusted networks cannot spend a third party's budget by forging its address.
Which proxies are trusted is `CHOREGOS_TRUSTED_PROXY_CIDRS` (or `CHOREGOS_TRUSTED_PROXY_HOPS`);
with neither, the TCP peer is the client and the header is ignored. An IPv6 client is keyed by
its /64, which one subscriber usually holds whole. The table holds at most `MAX_KEYS` keys,
evicting the least recently seen, and no request walks it.

What it stops: a scan of the sign-in routes or a flood of webhooks from one address. What it does
not stop: a distributed attack (that belongs to the ingress or a WAF), a flood of more than
`MAX_KEYS` distinct addresses, which evicts older windows, and a client inside a trusted network
that writes its own entries. With the chart's default private ranges, that last one includes runner
pods on any cluster with a private pod network: agent code may call the API directly, forge a
provider's address and spend its webhook budget (#1061). Authenticated routes do not come here:
their principal bounds them.
"""

from __future__ import annotations

import ipaddress
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from functools import lru_cache

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from .config import Settings, get_settings

PREFIXES = ("/api/v1/auth/", "/api/v1/webhooks/")
FENETRE_S = 60.0
#: The most keys one limiter holds; beyond it, the least recently seen key is evicted.
MAX_KEYS = 10_000
#: The longest key kept for a value that is not an IP address.
MAX_KEY_CHARS = 64

Network = ipaddress.IPv4Network | ipaddress.IPv6Network
Address = ipaddress.IPv4Address | ipaddress.IPv6Address


class Limiteur:
    def __init__(self, par_minute: int) -> None:
        self.par_minute = par_minute
        self._fenetres: OrderedDict[str, deque[float]] = OrderedDict()

    def admet(self, cle: str, maintenant: float | None = None) -> tuple[bool, int]:
        """Rend (admis, secondes avant la prochaine place)."""
        if self.par_minute <= 0:
            return True, 0
        t = time.monotonic() if maintenant is None else maintenant
        fenetre = self._fenetres.get(cle)
        if fenetre is None:
            fenetre = self._fenetres[cle] = deque()
        else:
            self._fenetres.move_to_end(cle)
        while fenetre and t - fenetre[0] >= FENETRE_S:
            fenetre.popleft()
        if len(fenetre) >= self.par_minute:
            return False, max(1, int(FENETRE_S - (t - fenetre[0])) + 1)
        fenetre.append(t)
        while len(self._fenetres) > MAX_KEYS:
            self._fenetres.popitem(last=False)
        return True, 0


@lru_cache(maxsize=16)
def _networks(cidrs: tuple[str, ...]) -> tuple[Network, ...]:
    """The trusted networks, parsed once (`Settings` has already refused a malformed one)."""
    return tuple(ipaddress.ip_network(cidr, strict=False) for cidr in cidrs)


def _ip(value: str) -> Address | None:
    """`value` as an IP address, an IPv4-mapped IPv6 address read as its IPv4 one; None otherwise."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _trusted(value: str, networks: tuple[Network, ...]) -> bool:
    address = _ip(value)
    return address is not None and any(address in network for network in networks)


def client_address(request: Request, settings: Settings) -> str:
    """The limiter's key: the address the nearest trusted proxy saw.

    - With `trusted_proxy_cidrs`: a peer outside them is the client. Otherwise the
      `X-Forwarded-For` entries are read right to left, and the first one that is not an address
      in a trusted network is the client; when every entry is trusted, the first one, or the peer
      when there are none.
    - With `trusted_proxy_hops` (and no CIDR): the entry the farthest of those proxies appended,
      or the peer when the header is shorter.
    - With neither: the peer, and the header is ignored.

    An IPv6 address is returned as its /64; any other value that is not an IP address is cut to
    `MAX_KEY_CHARS`.
    """
    peer = request.client.host if request.client else "unknown"
    # Several header lines are one list, in order (RFC 9110 5.3): a proxy may add its own line.
    forwarded = ",".join(request.headers.getlist("x-forwarded-for"))
    entries = [entry.strip() for entry in forwarded.split(",") if entry.strip()]
    address = peer
    if settings.trusted_proxy_cidrs:
        networks = _networks(tuple(settings.trusted_proxy_cidrs))
        if _trusted(peer, networks):
            untrusted = (entry for entry in reversed(entries) if not _trusted(entry, networks))
            address = next(untrusted, entries[0] if entries else peer)
    elif settings.trusted_proxy_hops > 0 and len(entries) >= settings.trusted_proxy_hops:
        address = entries[-settings.trusted_proxy_hops]
    ip = _ip(address)
    if isinstance(ip, ipaddress.IPv6Address):
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    if ip is not None:
        return str(ip)
    return address[:MAX_KEY_CHARS]


def installer(app: FastAPI, par_minute: int) -> Limiteur:
    limiteur = Limiteur(par_minute)

    @app.middleware("http")
    async def _limiter(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if request.url.path.startswith(PREFIXES):
            admis, attente = limiteur.admet(client_address(request, get_settings()))
            if not admis:
                return JSONResponse(
                    {
                        "type": "about:blank",
                        "title": "Too many requests",
                        "status": 429,
                        "detail": f"more than {par_minute} requests per minute from this address",
                        "instance": request.url.path,
                    },
                    status_code=429,
                    headers={"Retry-After": str(attente)},
                )
        return await call_next(request)

    return limiteur
