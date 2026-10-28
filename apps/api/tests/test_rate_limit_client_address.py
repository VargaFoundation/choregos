"""The sign-in and webhook rate limit keys on the address the nearest trusted proxy saw (S47-02).

Before, the key was the left-most `X-Forwarded-For` entry, which the client writes: rotating it
bought a fresh window on every request, and forging a provider's address spent that provider's
budget. The table also grew without bound, and past 10 000 keys every admitted request walked it.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

#: The chart's default trusted ranges (`choregos-api.trustedProxyCidrs`).
CHART_DEFAULT_RANGES = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"]


def _request(peer: str, *forwarded: str) -> Request:
    """A request as the API receives it: TCP peer `peer`, one `X-Forwarded-For` line per value."""
    headers = [(b"x-forwarded-for", value.encode()) for value in forwarded]
    scope = {"type": "http", "method": "POST", "path": "/api/v1/webhooks/jira", "headers": headers}
    return Request({**scope, "client": (peer, 1)})


def _settings(cidrs: list[str] | None = None, hops: int = 0) -> Any:
    from choregos_api.config import Settings

    return Settings(trusted_proxy_cidrs=cidrs or [], trusted_proxy_hops=hops)


async def test_a_forged_forwarded_for_buys_no_new_window(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import reset_settings_cache
    from choregos_api.main import create_app

    monkeypatch.setenv("CHOREGOS_RATE_LIMIT_PER_MINUTE", "2")
    monkeypatch.delenv("CHOREGOS_TRUSTED_PROXY_CIDRS", raising=False)
    monkeypatch.delenv("CHOREGOS_TRUSTED_PROXY_HOPS", raising=False)
    reset_settings_cache()
    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            codes = []
            for n in (1, 2, 3):
                forged = {"X-Forwarded-For": f"203.0.113.{n}"}
                codes.append((await c.post("/api/v1/webhooks/jira", json={}, headers=forged)).status_code)
        assert 429 not in codes[:2], codes
        assert codes[2] == 429, f"a forged X-Forwarded-For bought a new window: {codes}"
    finally:
        reset_settings_cache()


async def test_the_middleware_keys_on_the_trusted_proxy_address(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The middleware reads the trusted-proxy settings: the key is not the bare TCP peer."""
    from choregos_api.config import reset_settings_cache
    from choregos_api.main import create_app

    monkeypatch.setenv("CHOREGOS_RATE_LIMIT_PER_MINUTE", "1")
    # the test transport's TCP peer, named rather than left to httpx's default, is the trusted proxy
    proxy = ("10.0.0.7", 123)
    monkeypatch.setenv("CHOREGOS_TRUSTED_PROXY_CIDRS", '["10.0.0.0/8"]')
    monkeypatch.delenv("CHOREGOS_TRUSTED_PROXY_HOPS", raising=False)
    reset_settings_cache()
    try:
        app = create_app()
        transport = ASGITransport(app=app, client=proxy)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            codes = []
            for forwarded in ("203.0.113.1", "203.0.113.2", "198.51.100.9, 203.0.113.1"):
                headers = {"X-Forwarded-For": forwarded}
                codes.append((await c.post("/api/v1/webhooks/jira", json={}, headers=headers)).status_code)
        assert 429 not in codes[:2], f"two clients behind one trusted proxy shared a window: {codes}"
        assert codes[2] == 429, f"a new left-most entry bought 203.0.113.1 a new window: {codes}"
    finally:
        reset_settings_cache()


def test_a_forwarded_entry_with_a_port_is_keyed_by_its_address() -> None:
    """Azure Application Gateway writes `address:port`: the source port buys no new window."""
    from choregos_api.limiteur import client_address

    trusted = _settings(cidrs=CHART_DEFAULT_RANGES)
    keys = {client_address(_request("10.0.0.5", f"203.0.113.5:{port}"), trusted) for port in (54321, 54322)}
    assert keys == {"203.0.113.5"}
    # a trusted hop written with its port is still recognized as trusted
    assert client_address(_request("10.0.0.5", "203.0.113.5, 10.0.0.3:8080"), trusted) == "203.0.113.5"


def test_a_bracketed_ipv6_entry_is_keyed_by_its_64() -> None:
    from choregos_api.limiteur import client_address

    trusted = _settings(cidrs=CHART_DEFAULT_RANGES)
    written = ("[2001:db8:1:2::5]", "[2001:db8:1:2::9]:443", "[2001:db8:1:2::9]:444")
    keys = {client_address(_request("10.0.0.5", entry), trusted) for entry in written}
    assert keys == {"2001:db8:1:2::/64"}
    # a bare IPv6 address keeps its last group: it is not read as a port
    assert client_address(_request("10.0.0.5", "2001:db8:1:2::1"), trusted) == "2001:db8:1:2::/64"
    # not even when what precedes it parses: 2001:db8::1:2:3:4 lies in another /64
    assert client_address(_request("10.0.0.5", "2001:db8::1:2:3:4:5"), trusted) == "2001:db8:0:1::/64"


def test_behind_trusted_proxies_the_nearest_untrusted_entry_is_the_client() -> None:
    from choregos_api.limiteur import client_address

    trusted = _settings(cidrs=["10.0.0.0/8"])
    forwarded = "1.1.1.1, 198.51.100.7, 10.0.0.9"
    assert client_address(_request("10.0.0.5", forwarded), trusted) == "198.51.100.7"
    # a peer outside the trusted networks is the client, whatever it wrote
    assert client_address(_request("203.0.113.9", forwarded), trusted) == "203.0.113.9"
    # one proxy that appends one entry: the entry it appended, not the left-most
    assert client_address(_request("10.0.0.5", forwarded), _settings(hops=1)) == "10.0.0.9"
    # without a trusted proxy, the header is ignored
    assert client_address(_request("10.0.0.5", forwarded), _settings()) == "10.0.0.5"


def test_a_proxy_line_of_its_own_is_read_after_the_client_line() -> None:
    """A proxy may add its own `X-Forwarded-For` line rather than append to the client's."""
    from choregos_api.limiteur import client_address

    request = _request("10.0.0.5", "6.6.6.6", "198.51.100.7")
    assert client_address(request, _settings(cidrs=["10.0.0.0/8"])) == "198.51.100.7"


def test_behind_the_console_rewrite_the_browser_address_is_the_key() -> None:
    from choregos_api.limiteur import client_address

    request = _request("10.0.0.7", "203.0.113.5, 10.0.0.3")
    assert client_address(request, _settings(cidrs=CHART_DEFAULT_RANGES)) == "203.0.113.5"


def test_ipv6_clients_are_keyed_by_their_64() -> None:
    from choregos_api.limiteur import client_address

    keys = {client_address(_request(peer), _settings()) for peer in ("2001:db8:1:2::5", "2001:db8:1:2::9")}
    assert keys == {"2001:db8:1:2::/64"}
    # an IPv4 client written as an IPv4-mapped IPv6 address keeps its own key, not `::/64`
    assert client_address(_request("::ffff:203.0.113.5"), _settings()) == "203.0.113.5"


def test_the_limiter_holds_at_most_ten_thousand_keys() -> None:
    from choregos_api.limiteur import Limiteur

    limiter = Limiteur(5)
    for n in range(12_000):
        assert limiter.admet(f"key-{n}", 0.0)[0]
    assert len(limiter._fenetres) <= 10_000
    assert "key-11999" in limiter._fenetres, "the newest key was evicted"
    assert "key-0" not in limiter._fenetres, "the least recently seen key goes first"


def test_a_key_seen_again_is_evicted_last() -> None:
    """A throttled client that keeps knocking keeps its window while silent keys go."""
    from choregos_api.limiteur import Limiteur

    limiter = Limiteur(1)
    for n in range(10_000):
        limiter.admet(f"key-{n}", 0.0)
    assert not limiter.admet("key-0", 1.0)[0], "key-0 has spent its minute"
    limiter.admet("newcomer", 1.0)
    assert "key-0" in limiter._fenetres and "key-1" not in limiter._fenetres
    assert not limiter.admet("key-0", 2.0)[0], "eviction gave a throttled key a fresh window"


class _Unwalkable(OrderedDict[str, deque[float]]):
    """A table that fails the request walking it."""

    def __iter__(self) -> Iterator[str]:
        raise AssertionError("a request walked the whole table")

    def items(self) -> Any:
        raise AssertionError("a request walked the whole table")

    def keys(self) -> Any:
        raise AssertionError("a request walked the whole table")

    def values(self) -> Any:
        raise AssertionError("a request walked the whole table")


def test_no_request_walks_the_table() -> None:
    from choregos_api.limiteur import Limiteur

    limiter = Limiteur(5)
    limiter._fenetres = _Unwalkable()
    for n in range(10_100):
        limiter.admet(f"key-{n}", 0.0)
    assert len(limiter._fenetres) == 10_000


def test_a_malformed_trusted_proxy_network_is_refused() -> None:
    from choregos_api.config import Settings

    with pytest.raises(ValueError, match="CHOREGOS_TRUSTED_PROXY_CIDRS"):
        Settings(trusted_proxy_cidrs=["not-a-network"])
    with pytest.raises(ValueError, match="not both"):
        Settings(trusted_proxy_cidrs=["10.0.0.0/8"], trusted_proxy_hops=1)
