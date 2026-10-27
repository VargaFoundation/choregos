# SPDX-License-Identifier: Apache-2.0
"""The outbound-proxy relay (S40-26d, ADR 0091): its internal route, its token, and the relay itself.

The relay runs for real on 127.0.0.1, in front of a scripted upstream proxy and a scripted target, both
listening on 127.0.0.1 too. The resolver and the connector are injected: a public address stands for the
target in the direct mode, so the private-address refusal can be seen on the path it guards."""

from __future__ import annotations

import asyncio
import base64
import ssl
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field

import httpx
import jwt
import pytest
from choregos_api.relay import ApiProxySource, CachedProxySource, ProxySourceError, Relay
from choregos_api.relay.__main__ import settings_from
from choregos_api.relay.server import MAX_HEAD_BYTES
from choregos_api.security import (
    RELAY_TOKEN_AUDIENCE,
    mint_relay_token,
    mint_run_token,
    run_token_keys,
    verify_relay_token,
    verify_run_token,
)
from choregos_core.outbound_proxy import ProxyConfig
from httpx import AsyncClient

from .test_outbound_proxy import BODY, PASSWORD, Net, _audit, _put

ROUTE = "/api/v1/internal/orgs/varga/outbound-proxy"
CLIENT_TOKEN = "relay-client-token-0123456789"
PUBLIC = "93.184.216.34"


@pytest.fixture
def net() -> Iterator[Net]:
    """`test_outbound_proxy.net`: a recording proxy transport and a fresh secret store."""
    from choregos_adapters.http import proxy as proxy_transports
    from choregos_api.secret_store import FakeSecretStore, set_secret_store

    seen = Net()
    proxy_transports.set_proxy_transport_factory(seen.proxy_factory)
    set_secret_store(FakeSecretStore())
    yield seen
    proxy_transports.set_proxy_transport_factory(None)
    set_secret_store(None)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ───────────────────────────── the token ─────────────────────────────


def test_a_relay_token_names_one_organisation_and_always_expires() -> None:
    assert verify_relay_token(mint_relay_token("varga", days=1)) == "varga"
    for days in (0, 401):
        with pytest.raises(ValueError, match="days"):
            mint_relay_token("varga", days=days)
    private, _ = run_token_keys()
    endless = jwt.encode(
        {"iss": "choregos-api", "aud": RELAY_TOKEN_AUDIENCE, "sub": "varga"}, private, "ES256"
    )
    with pytest.raises(jwt.MissingRequiredClaimError):
        verify_relay_token(endless)


def test_a_run_token_and_a_relay_token_are_not_interchangeable() -> None:
    run = mint_run_token("run-1", project_slug="p", work_item_key="K-1", ttl_minutes=5)
    with pytest.raises(jwt.InvalidAudienceError):
        verify_relay_token(run)
    with pytest.raises(jwt.InvalidAudienceError):
        verify_run_token(mint_relay_token("varga", days=1))


# ───────────────────────────── the route ─────────────────────────────


async def test_the_route_gives_the_relay_its_proxy_uncached_and_unaudited(
    client: AsyncClient, admin: str, net: Net
) -> None:
    assert (await _put(client)).status_code == 200
    before = len(await _audit("outbound_proxy.update"))
    answer = await client.get(f"{ROUTE}/search_engines", headers=_bearer(mint_relay_token("varga", days=1)))
    assert answer.status_code == 200, answer.text
    assert answer.headers["cache-control"] == "no-store"
    assert answer.json() == {
        "proxy": {
            "endpoint": BODY["endpoint"],
            "scheme": "http",
            "username": BODY["username"],
            "password": PASSWORD,
        }
    }
    assert len(await _audit("outbound_proxy.update")) == before
    assert not [a for a in ("outbound_proxy.read", "outbound_proxy.relay") if await _audit(a)]


async def test_the_route_answers_null_when_no_proxy_applies(
    client: AsyncClient, admin: str, net: Net
) -> None:
    token = _bearer(mint_relay_token("varga", days=1))
    assert (await client.get(f"{ROUTE}/search_engines", headers=token)).json() == {"proxy": None}
    await _put(client, applies_to=["web_fetch"])
    assert (await client.get(f"{ROUTE}/search_engines", headers=token)).json() == {"proxy": None}
    await _put(client, enabled=False, password=None, applies_to=["search_engines"])
    assert (await client.get(f"{ROUTE}/search_engines", headers=token)).json() == {"proxy": None}


async def test_the_route_refuses_every_credential_but_this_organisations_relay_token(
    client: AsyncClient, admin: str, net: Net
) -> None:
    await _put(client)
    url = f"{ROUTE}/search_engines"
    assert (await client.get(url)).status_code == 401, "an org_admin's session is not a relay token"
    run = mint_run_token("run-1", project_slug="p", work_item_key="K-1", ttl_minutes=5)
    for refused in (run, mint_relay_token("other", days=1), "garbage"):
        answer = await client.get(url, headers=_bearer(refused))
        assert answer.status_code == 403, answer.text
        assert PASSWORD not in answer.text
    assert (
        await client.get(f"{ROUTE}/agent_runs", headers=_bearer(mint_relay_token("varga", days=1)))
    ).status_code == 422


async def test_the_route_is_a_503_when_the_proxy_applies_but_has_lost_its_password(
    client: AsyncClient, admin: str, net: Net
) -> None:
    from choregos_api.secret_store import FakeSecretStore, set_secret_store

    await _put(client)
    set_secret_store(FakeSecretStore())
    answer = await client.get(f"{ROUTE}/search_engines", headers=_bearer(mint_relay_token("varga", days=1)))
    assert answer.status_code == 503, answer.text
    assert "gate.proxy.example:7000" in answer.text


# ───────────────────────────── the source ─────────────────────────────


async def test_the_api_source_reads_the_route_and_fails_on_anything_but_a_200() -> None:
    seen: list[httpx.Request] = []
    answers = {
        "search_engines": httpx.Response(
            200, json={"proxy": {"endpoint": "gate:7000", "scheme": "http", "username": "u", "password": "p"}}
        ),
        "web_search": httpx.Response(200, json={"proxy": None}),
        "web_fetch": httpx.Response(503, json={"proxy": None}),  # a 503 is never "direct"
        "knowledge_web": httpx.Response(200, json={}),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answers[request.url.path.rsplit("/", 1)[1]]

    source = ApiProxySource("http://api/api/v1/", "varga", "tok", transport=httpx.MockTransport(handler))
    assert await source.get("search_engines") == ProxyConfig("gate:7000", "http", "u", "p")
    assert seen[0].url.path == "/api/v1/internal/orgs/varga/outbound-proxy/search_engines"
    assert seen[0].headers["authorization"] == "Bearer tok"
    assert await source.get("web_search") is None
    for failing in ("web_fetch", "knowledge_web", "agent_runs"):
        with pytest.raises(ProxySourceError):
            await source.get(failing)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ProxySourceError, match="ConnectError"):
        await ApiProxySource("http://api", "varga", "tok", transport=httpx.MockTransport(down)).get(
            "web_search"
        )


class Scripted:
    def __init__(self) -> None:
        self.answers: list[ProxyConfig | Exception | None] = []
        self.calls = 0

    async def get(self, kind: str) -> ProxyConfig | None:
        self.calls += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


async def test_the_cache_lasts_its_ttl_and_never_outlives_a_failure() -> None:
    now = [0.0]
    inner = Scripted()
    cached = CachedProxySource(inner, ttl_s=30, clock=lambda: now[0])
    proxy = ProxyConfig("gate:7000", "http", "u", "p")
    inner.answers = [proxy, None, ProxySourceError("down"), proxy]
    assert await cached.get("web_search") == proxy
    now[0] = 29
    assert await cached.get("web_search") == proxy and inner.calls == 1
    now[0] = 30
    assert await cached.get("web_search") is None, "a proxy removed is no longer used after the TTL"
    now[0] = 61
    with pytest.raises(ProxySourceError):
        await cached.get("web_search")
    assert await cached.get("web_search") == proxy and inner.calls == 4, "a failure is not cached"


def test_the_relay_refuses_to_start_without_its_settings() -> None:
    full = {
        "CHOREGOS_RELAY_ORG": "varga",
        "CHOREGOS_RELAY_API_URL": "http://api",
        "CHOREGOS_RELAY_API_TOKEN": "t",
        "CHOREGOS_RELAY_CLIENT_TOKEN": CLIENT_TOKEN,
    }
    assert settings_from(full).port == 3128
    with pytest.raises(SystemExit, match="CHOREGOS_RELAY_API_TOKEN"):
        settings_from({k: v for k, v in full.items() if k != "CHOREGOS_RELAY_API_TOKEN"})
    with pytest.raises(SystemExit, match="16 characters"):
        settings_from(full | {"CHOREGOS_RELAY_CLIENT_TOKEN": "short"})


# ───────────────────────────── the relay ─────────────────────────────


@dataclass
class Upstream:
    """A scripted proxy (`mode`: `ok`, `407`, `403`) and a scripted target, each an echo once open."""

    mode: str = "ok"
    heads: list[bytes] = field(default_factory=list)
    target_heads: list[bytes] = field(default_factory=list)
    connects: list[tuple[str, int]] = field(default_factory=list)
    proxy_port: int = 0
    target_port: int = 0

    async def proxy(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        self.heads.append(head)
        if self.mode != "ok":
            writer.write(f"HTTP/1.1 {self.mode} No\r\nContent-Length: 0\r\n\r\n".encode())
        elif head.startswith(b"CONNECT"):
            writer.write(b"HTTP/1.1 200 OK\r\n\r\n")
            await writer.drain()
            await _echo(reader, writer)
            return
        else:
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 6\r\n\r\nproxy!")
        await writer.drain()
        writer.close()

    async def target(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        first = await reader.read(4096)
        self.target_heads.append(first)
        if first.startswith(b"GET "):
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 7\r\n\r\ndirect!")
        else:
            writer.write(first)
            await _echo(reader, writer)
            return
        await writer.drain()
        writer.close()


async def _echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while chunk := await reader.read(4096):
        writer.write(chunk)
        await writer.drain()
    writer.close()


@pytest.fixture
async def upstream() -> AsyncIterator[Upstream]:
    seen = Upstream()
    proxy = await asyncio.start_server(seen.proxy, "127.0.0.1", 0)
    target = await asyncio.start_server(seen.target, "127.0.0.1", 0)
    seen.proxy_port = proxy.sockets[0].getsockname()[1]
    seen.target_port = target.sockets[0].getsockname()[1]
    async with proxy, target:
        yield seen


@dataclass
class Bench:
    upstream: Upstream
    source: Scripted
    port: int
    addresses: list[str]


@pytest.fixture
async def bench(upstream: Upstream) -> AsyncIterator[Bench]:
    source = Scripted()
    addresses = [PUBLIC]

    async def resolver(host: str, port: int) -> list[str]:
        return list(addresses)

    async def connect(
        host: str, port: int, tls: ssl.SSLContext | None, name: str | None
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        upstream.connects.append((host, port))
        if port == 1:
            raise ConnectionRefusedError("nothing listens")
        if host == PUBLIC:
            return await asyncio.open_connection("127.0.0.1", upstream.target_port)
        return await asyncio.open_connection(host, port)

    relay = Relay(source, CLIENT_TOKEN, resolver=resolver, connect=connect)
    server = await asyncio.start_server(relay.handle, "127.0.0.1", 0, limit=MAX_HEAD_BYTES)
    async with server:
        yield Bench(upstream, source, server.sockets[0].getsockname()[1], addresses)


def _auth(user: str = "search_engines", secret: str = CLIENT_TOKEN) -> str:
    return "Proxy-Authorization: Basic " + base64.b64encode(f"{user}:{secret}".encode()).decode()


async def _ask(bench: Bench, request: str, then: bytes = b"") -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", bench.port)
    writer.write(request.encode() + then)
    await writer.drain()
    if then:
        writer.write_eof()
    try:
        return await asyncio.wait_for(reader.read(-1), 5)
    finally:
        writer.close()


def _connect(user: str = "search_engines", secret: str = CLIENT_TOKEN) -> str:
    return f"CONNECT example.org:443 HTTP/1.1\r\nHost: example.org:443\r\n{_auth(user, secret)}\r\n\r\n"


def _proxy(bench: Bench) -> ProxyConfig:
    return ProxyConfig(f"127.0.0.1:{bench.upstream.proxy_port}", "http", "user-country-fr", PASSWORD)


async def test_a_client_without_the_token_or_with_another_kind_is_a_407_and_nothing_is_asked(
    bench: Bench,
) -> None:
    for request in (
        "CONNECT example.org:443 HTTP/1.1\r\n\r\n",
        _connect(secret="wrong-token-0123456789"),
        _connect(user="agent_runs"),
    ):
        answer = await _ask(bench, request)
        assert answer.startswith(b"HTTP/1.1 407"), answer
        assert b"Proxy-Authenticate: Basic" in answer
    assert bench.source.calls == 0 and bench.upstream.connects == []


async def test_through_the_proxy_connect_names_the_host_and_carries_the_organisations_credentials(
    bench: Bench,
) -> None:
    bench.source.answers = [_proxy(bench)]
    answer = await _ask(bench, _connect(), b"ping")
    assert answer == b"HTTP/1.1 200 Connection established\r\n\r\nping"
    head = bench.upstream.heads[0].decode()
    assert head.startswith("CONNECT example.org:443 HTTP/1.1\r\n"), "by NAME: the proxy resolves"
    sent = head.split("Proxy-Authorization: Basic ", 1)[1].split("\r\n", 1)[0]
    assert base64.b64decode(sent).decode() == f"user-country-fr:{PASSWORD}"
    assert CLIENT_TOKEN not in head, "the relay's client token never leaves the relay"
    assert bench.upstream.target_heads == [], "nothing went direct"


@pytest.mark.parametrize("mode", ["407", "403"])
async def test_a_proxy_that_refuses_is_a_502_naming_it_and_never_a_direct_call(
    bench: Bench, mode: str
) -> None:
    bench.upstream.mode = mode
    bench.source.answers = [_proxy(bench)]
    answer = await _ask(bench, _connect())
    assert answer.startswith(b"HTTP/1.1 502"), answer
    assert f"127.0.0.1:{bench.upstream.proxy_port}".encode() in answer
    assert PASSWORD.encode() not in answer
    assert (b"refused the credentials (407)" if mode == "407" else b"(HTTP 403)") in answer
    assert bench.upstream.target_heads == [] and (PUBLIC, 443) not in bench.upstream.connects


async def test_an_unreachable_proxy_is_a_502_and_never_a_direct_call(bench: Bench) -> None:
    bench.source.answers = [ProxyConfig("127.0.0.1:1", "http", "u", PASSWORD)]
    answer = await _ask(bench, _connect())
    assert answer.startswith(b"HTTP/1.1 502") and b"127.0.0.1:1" in answer, answer
    assert PASSWORD.encode() not in answer
    assert (PUBLIC, 443) not in bench.upstream.connects


async def test_when_the_relay_cannot_tell_it_sends_nothing(bench: Bench) -> None:
    bench.source.answers = [ProxySourceError("the API answered HTTP 503")]
    answer = await _ask(bench, _connect())
    assert answer.startswith(b"HTTP/1.1 502") and b"nothing is sent" in answer, answer
    assert bench.upstream.connects == []


async def test_without_a_proxy_it_goes_direct_pinned_to_the_address_it_checked(bench: Bench) -> None:
    bench.source.answers = [None]
    answer = await _ask(bench, _connect(), b"ping")
    assert answer == b"HTTP/1.1 200 Connection established\r\n\r\nping"
    assert bench.upstream.connects == [(PUBLIC, 443)]
    assert bench.upstream.heads == [], "the proxy was not used"


@pytest.mark.parametrize("addresses", [["10.0.0.5"], [PUBLIC, "127.0.0.1"], ["169.254.169.254"], ["fd00::1"]])
async def test_direct_refuses_a_private_address(bench: Bench, addresses: list[str]) -> None:
    bench.source.answers = [None]
    bench.addresses[:] = addresses
    answer = await _ask(bench, _connect())
    assert answer.startswith(b"HTTP/1.1 403") and b"private address" in answer, answer
    assert bench.upstream.connects == []


async def test_an_http_target_through_the_proxy_is_absolute_form_without_the_client_token(
    bench: Bench,
) -> None:
    bench.source.answers = [_proxy(bench)]
    request = (
        "GET http://example.org/q?x=1 HTTP/1.1\r\nHost: example.org\r\n"
        f"{_auth('web_search')}\r\nProxy-Connection: keep-alive\r\n\r\n"
    )
    answer = await _ask(bench, request)
    assert answer.endswith(b"proxy!"), answer
    head = bench.upstream.heads[0].decode()
    assert head.startswith("GET http://example.org/q?x=1 HTTP/1.1\r\n")
    assert (
        CLIENT_TOKEN not in head
        and base64.b64encode(f"web_search:{CLIENT_TOKEN}".encode()).decode() not in head
    )
    assert "Connection: close" in head and "Proxy-Connection" not in head


async def test_an_http_target_direct_is_origin_form_without_the_client_token(bench: Bench) -> None:
    bench.source.answers = [None]
    request = f"GET http://example.org/q?x=1 HTTP/1.1\r\nHost: example.org\r\n{_auth('web_search')}\r\n\r\n"
    answer = await _ask(bench, request)
    assert answer.endswith(b"direct!"), answer
    head = bench.upstream.target_heads[0].decode()
    assert head.startswith("GET /q?x=1 HTTP/1.1\r\n") and "Proxy-Authorization" not in head
    assert bench.upstream.connects == [(PUBLIC, 80)]


async def test_an_https_url_in_absolute_form_is_refused(bench: Bench) -> None:
    answer = await _ask(bench, f"GET https://example.org/ HTTP/1.1\r\n{_auth()}\r\n\r\n")
    assert answer.startswith(b"HTTP/1.1 400"), answer
    assert bench.source.calls == 0
