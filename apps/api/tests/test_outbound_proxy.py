"""An organisation's outbound proxy (S40-26b, ADR 0087): the routes, the sealed password, the resolver and
its wiring into `web_fetch`, the `web_search` backends and the knowledge web crawl. No network: a
recording `MockTransport` stands for the proxy, and one test runs a real listener on 127.0.0.1 to see the
`CONNECT` line a proxy receives."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from choregos_adapters.http import proxy as proxy_transports
from choregos_core.outbound_proxy import OutboundProxyError, ProxyConfig, use_proxies
from httpx import AsyncClient

from .conftest import login
from .test_web_fetch_tool import HTML, PAGE, PUBLIC
from .test_web_fetch_tool import _ready as _fetch_ready
from .test_web_search_tool import QUERY, ZERO
from .test_web_search_tool import _ready as _search_ready

URL = "/api/v1/orgs/varga/outbound-proxy"
PASSWORD = "p@ss=w/rd%1&x"
BODY: dict[str, Any] = {
    "enabled": True,
    "endpoint": "gate.proxy.example:7000",
    "scheme": "http",
    "username": "user-acme-country-fr",
    "password": PASSWORD,
}
ENCODED = "p%40ss%3Dw%2Frd%251%26x"


class Net:
    """What the tests' proxy and the direct path saw. `proxy_mode`: `ok`, `unreachable`, `407`."""

    def __init__(self) -> None:
        self.via_proxy: list[tuple[str, httpx.Request]] = []
        self.direct: list[httpx.Request] = []
        self.proxy_mode = "ok"
        self.exit_ip = "203.0.113.9"
        self.pages: dict[str, httpx.Response] = {}

    def proxy_factory(self, proxy: ProxyConfig) -> httpx.AsyncBaseTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            if self.proxy_mode == "unreachable":
                raise httpx.ConnectError("refused", request=request)
            self.via_proxy.append((proxy.url, request))
            if self.proxy_mode == "407":
                return httpx.Response(407, headers={"proxy-authenticate": 'Basic realm="x"'})
            return self.answer(request)

        return httpx.MockTransport(handler)

    def direct_transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.direct.append(request)
            return self.answer(request)

        return httpx.MockTransport(handler)

    def answer(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "ipinfo.io":
            return httpx.Response(200, json={"ip": self.exit_ip, "country": "FR"})
        if request.url.host == "api.search.brave.com":
            hit = {"title": "T", "url": "https://docs.example.org/x", "description": "S"}
            return httpx.Response(200, json={"web": {"results": [hit]}})
        return self.pages.get(str(request.url)) or httpx.Response(
            200, headers={"content-type": "text/html"}, text=HTML
        )


@pytest.fixture
def net(monkeypatch: pytest.MonkeyPatch) -> Iterator[Net]:
    from choregos_api.secret_store import FakeSecretStore, set_secret_store

    seen = Net()
    proxy_transports.set_proxy_transport_factory(seen.proxy_factory)
    set_secret_store(FakeSecretStore())
    yield seen
    proxy_transports.set_proxy_transport_factory(None)
    set_secret_store(None)


class Envelope(dict[str, Any]):
    """A tool's answer: HTTP 200 carrying `status_code`, `result`."""

    @property
    def status_code(self) -> int:
        return int(self["status_code"])

    @property
    def text(self) -> str:
        return json.dumps(self)


async def _put(client: AsyncClient, **over: Any) -> Any:
    body = {**BODY, **over}
    return await client.put(URL, json={k: v for k, v in body.items() if v is not None})


async def _audit(action: str) -> list[dict[str, Any]]:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (await session.execute(select(AuditLog).where(AuditLog.action == action))).scalars()
        return [dict(r.payload) for r in rows]


async def _resolve(org_slug: str, kind: str) -> ProxyConfig | None:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from choregos_api.services.outbound_proxy import outbound_proxy_for
    from sqlalchemy import select

    async with session_scope() as session:
        org = (await session.execute(select(Organization).where(Organization.slug == org_slug))).scalar_one()
        return await outbound_proxy_for(session, org.id, kind)


# ───────────────────────────── the routes ─────────────────────────────


async def test_the_password_is_sealed_and_never_returned_nor_audited(
    client: AsyncClient, admin: str, net: Net
) -> None:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from choregos_api.secret_store import get_secret_store
    from sqlalchemy import select

    assert (await client.get(URL)).json() == {"proxy": None, "updated_by": None, "updated_at": None}
    saved = await _put(client)
    assert saved.status_code == 200, saved.text
    read = await client.get(URL)
    for answer in (saved, read):
        assert PASSWORD not in answer.text and ENCODED not in answer.text
        assert answer.json()["proxy"]["password_set"] is True
        assert "password" not in answer.json()["proxy"]
    assert read.json()["proxy"]["applies_to"] == [
        "web_fetch",
        "web_search",
        "knowledge_web",
        "search_engines",
    ]
    assert read.json()["updated_by"] == admin
    # sealed under the derived name, not in the organisation's settings, not in the audit
    async with session_scope() as session:
        org = (await session.execute(select(Organization))).scalar_one()
        assert PASSWORD not in json.dumps(org.settings)
        values = await get_secret_store().get_many(session, org.id, ["org/outbound-proxy/password"])
    assert values == {"org/outbound-proxy/password": PASSWORD}
    rows = await _audit("outbound_proxy.update")
    assert len(rows) == 1 and PASSWORD not in json.dumps(rows) and ENCODED not in json.dumps(rows)
    assert rows[0]["endpoint"] == BODY["endpoint"] and rows[0]["username"] == BODY["username"]


async def test_the_password_is_kept_when_absent_and_required_the_first_time(
    client: AsyncClient, admin: str, net: Net
) -> None:
    first = await _put(client, password=None)
    assert first.status_code == 422, first.text
    assert (await client.get(URL)).json()["proxy"] is None, "a refused first write stores nothing"
    assert (await _put(client)).status_code == 200
    again = await _put(client, password=None, enabled=False, username="user-b")
    assert again.status_code == 200, again.text
    assert again.json()["proxy"]["password_set"] is True and again.json()["proxy"]["enabled"] is False
    proxy = await _resolve_stored()
    assert proxy.password == PASSWORD and proxy.username == "user-b"


async def _resolve_stored() -> ProxyConfig:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from choregos_api.services.outbound_proxy import stored_config
    from sqlalchemy import select

    async with session_scope() as session:
        org = (await session.execute(select(Organization))).scalar_one()
        found = await stored_config(session, org)
    assert found is not None
    return found


async def test_a_deployment_without_a_secrets_key_is_a_409_and_stores_nothing(
    client: AsyncClient, admin: str, net: Net
) -> None:
    from choregos_api.secret_store import FakeSecretStore, set_secret_store

    set_secret_store(FakeSecretStore(available=False))
    refused = await _put(client)
    assert refused.status_code == 409, refused.text
    assert (await client.get(URL)).json()["proxy"] is None


async def test_the_endpoint_and_the_kinds_are_bounded(client: AsyncClient, admin: str, net: Net) -> None:
    for endpoint in (
        "https://gate.example:7000",
        "gate.example",
        "u:p@gate.example:7000",
        "gate.example:99999",
    ):
        assert (await _put(client, endpoint=endpoint)).status_code == 422, endpoint
    assert (await _put(client, applies_to=[])).status_code == 422
    assert (await _put(client, applies_to=["agent_runs"])).status_code == 422
    assert (await _put(client, scheme="socks5")).status_code == 422


async def test_delete_removes_the_setting_and_the_sealed_password(
    client: AsyncClient, admin: str, net: Net
) -> None:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from choregos_api.secret_store import get_secret_store
    from sqlalchemy import select

    await _put(client)
    assert (await client.delete(URL)).status_code == 204
    assert (await client.delete(URL)).status_code == 204, "idempotent"
    assert (await client.get(URL)).json()["proxy"] is None
    async with session_scope() as session:
        org = (await session.execute(select(Organization))).scalar_one()
        assert await get_secret_store().meta(session, org.id, ["org/outbound-proxy/password"]) == {}
    assert await _resolve("varga", "web_fetch") is None
    deleted = await _audit("outbound_proxy.delete")
    assert deleted[0]["endpoint"] == BODY["endpoint"] and PASSWORD not in json.dumps(deleted)


async def test_every_route_needs_org_admin(client: AsyncClient, org: str, net: Net) -> None:
    await login(client, "admin@varga.dev")
    await _put(client)
    client.cookies.clear()
    await login(client, "someone@elsewhere.dev")
    assert (await client.get(URL)).status_code == 403
    assert (await _put(client, endpoint="evil.example:1")).status_code == 403
    assert (await client.post(f"{URL}/test")).status_code == 403
    assert (await client.delete(URL)).status_code == 403
    client.cookies.clear()
    await login(client, "admin@varga.dev")
    assert (await client.get(URL)).json()["proxy"]["endpoint"] == BODY["endpoint"], "nothing was changed"


# ───────────────────────────── the test action ─────────────────────────────


async def test_the_test_action_fetches_the_echo_through_the_proxy_and_ignores_enabled(
    client: AsyncClient, admin: str, net: Net
) -> None:
    assert (await client.post(f"{URL}/test")).status_code == 404, "none set"
    await _put(client, enabled=False)
    result = await client.post(f"{URL}/test")
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["ok"] is True and body["exit_ip"] == "203.0.113.9" and body["country"] == "FR"
    assert body["target"] == "https://ipinfo.io/json" and body["error"] is None
    assert [str(r.url) for _, r in net.via_proxy] == ["https://ipinfo.io/json"]
    assert net.direct == []
    tested = await _audit("outbound_proxy.test")
    assert tested[0]["ok"] is True and PASSWORD not in json.dumps(tested)


async def test_a_failing_proxy_is_a_200_that_names_it_and_never_the_password(
    client: AsyncClient, admin: str, net: Net
) -> None:
    await _put(client)
    for mode in ("unreachable", "407"):
        net.proxy_mode = mode
        answer = await client.post(f"{URL}/test")
        assert answer.status_code == 200, answer.text
        body = answer.json()
        assert body["ok"] is False and body["exit_ip"] is None
        assert "http://gate.proxy.example:7000" in body["error"], body
        assert (
            PASSWORD not in answer.text
            and ENCODED not in answer.text
            and BODY["username"] not in body["error"]
        )


# ───────────────────────────── the resolver ─────────────────────────────


async def test_the_proxy_url_percent_encodes_its_credentials() -> None:
    proxy = ProxyConfig("gate.proxy.example:7000", "http", "user@acme:fr", PASSWORD)
    assert proxy.url == f"http://user%40acme%3Afr:{ENCODED}@gate.proxy.example:7000"
    assert httpx.Proxy(proxy.url).auth == ("user@acme:fr", PASSWORD), "what httpx sends is what was typed"
    assert PASSWORD not in repr(proxy) and PASSWORD not in proxy.label


async def test_the_resolver_honours_enabled_and_applies_to(client: AsyncClient, admin: str, net: Net) -> None:
    await _put(client, applies_to=["web_fetch", "knowledge_web"])
    assert (await _resolve("varga", "web_fetch")) == ProxyConfig(
        "gate.proxy.example:7000", "http", BODY["username"], PASSWORD
    )
    assert await _resolve("varga", "knowledge_web") is not None
    assert await _resolve("varga", "web_search") is None, "a kind outside applies_to goes direct"
    await _put(client, enabled=False, password=None)
    assert await _resolve("varga", "web_fetch") is None, "disabled goes direct"


async def test_a_proxy_that_applies_but_has_lost_its_password_fails_closed(
    client: AsyncClient, admin: str, net: Net
) -> None:
    from choregos_api.secret_store import FakeSecretStore, set_secret_store

    await _put(client)
    set_secret_store(FakeSecretStore())  # the sealed value is gone
    with pytest.raises(OutboundProxyError, match=r"http://gate\.proxy\.example:7000"):
        await _resolve("varga", "web_fetch")


async def test_an_organisation_never_gets_anothers_proxy(client: AsyncClient, admin: str, net: Net) -> None:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        session.add(Organization(slug="other", name="Other"))
    await _put(client)
    assert (await _resolve("varga", "web_fetch")) is not None
    assert await _resolve("other", "web_fetch") is None
    assert await _resolve("other", "web_search") is None


# ───────────────────────────── web_fetch ─────────────────────────────


@pytest.fixture
def web(net: Net) -> Iterator[Any]:
    from choregos_api.services import web_fetch

    fake = web_fetch.FakeWebClient()

    async def resolver(host: str) -> list[str]:
        return {"internal.example.com": ["10.0.0.5"]}.get(host, [PUBLIC])

    web_fetch.set_web_client(fake)
    web_fetch.set_resolver(resolver)
    web_fetch.set_clock(lambda: 1000.0)
    web_fetch.clear_robots()
    yield fake
    web_fetch.set_web_client(None)
    web_fetch.set_resolver(None)
    web_fetch.set_clock(None)
    web_fetch.clear_robots()


async def _read(client: AsyncClient, base: str, headers: dict[str, str], url: str = PAGE) -> Any:
    answer = await client.post(f"{base}/web_fetch", headers=headers, json={"url": url})
    assert answer.status_code == 200, answer.text
    return Envelope(answer.json())


async def test_web_fetch_goes_through_the_proxy_when_it_applies_and_direct_when_not(
    client: AsyncClient, project: dict[str, Any], web: Any, net: Net
) -> None:
    web.add(PAGE, HTML)
    web.add("https://docs.example.com/robots.txt", "", content_type="text/plain")
    await _put(client, applies_to=["web_search"])
    base, headers = await _fetch_ready(client, project)
    assert (await _read(client, base, headers)).status_code == 200
    assert web.proxies and all(p is None for p in web.proxies), "web_fetch is not in applies_to: direct"

    web.requests.clear(), web.proxies.clear()
    await _put(client, applies_to=["web_fetch"], password=None, enabled=False)
    assert (await _read(client, base, headers)).status_code == 200
    assert all(p is None for p in web.proxies), "disabled: direct"

    web.requests.clear(), web.proxies.clear()
    await _put(client, applies_to=["web_fetch"], password=None)
    answer = await _read(client, base, headers)
    assert answer.status_code == 200, answer.text
    assert web.proxies, "the page was read"
    assert all(p is not None and p.endpoint == BODY["endpoint"] for p in web.proxies)


async def test_through_the_proxy_the_target_is_still_checked_for_private_addresses(
    client: AsyncClient, project: dict[str, Any], web: Any, net: Net
) -> None:
    await _put(client)
    base, headers = await _fetch_ready(client, project, ["internal.example.com"])
    refused = await _read(client, base, headers, "https://internal.example.com/admin")
    assert refused.status_code == 403, refused.text
    assert "private address" in refused.text
    assert web.requests == [] and net.via_proxy == [], "nothing was sent, not even to the proxy"


async def test_web_fetch_fails_closed_when_the_proxy_fails_and_never_goes_direct(
    client: AsyncClient, project: dict[str, Any], web: Any, net: Net, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.services import web_fetch

    web_fetch.set_web_client(web_fetch.HttpWebClient(transport=net.direct_transport()))
    await _put(client)
    base, headers = await _fetch_ready(client, project)
    for mode in ("unreachable", "407"):
        net.proxy_mode = mode
        web_fetch.clear_robots()
        answer = await _read(client, base, headers)
        assert answer.status_code in (403, 502), answer.text  # robots refusal or the page: both refuse
        assert "http://gate.proxy.example:7000" in answer.text, answer.text
        assert PASSWORD not in answer.text and ENCODED not in answer.text
    assert net.direct == [], "no direct call, to the page or to anything"


async def test_web_fetch_without_its_sealed_password_refuses_rather_than_going_direct(
    client: AsyncClient, project: dict[str, Any], web: Any, net: Net
) -> None:
    from choregos_api.secret_store import FakeSecretStore, set_secret_store

    await _put(client)
    base, headers = await _fetch_ready(client, project)
    set_secret_store(FakeSecretStore())
    answer = await _read(client, base, headers)
    assert answer.status_code == 502 and "http://gate.proxy.example:7000" in answer.text
    assert web.requests == []


async def test_web_fetch_sends_connect_by_name_with_the_decoded_credentials() -> None:
    """A real listener stands for the proxy: the `CONNECT` line names the host, not the address that was
    checked locally, and the credentials arrive as typed (not as percent-encoded)."""
    from choregos_api.services.web_fetch import HttpWebClient

    lines: list[list[str]] = []

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = (await reader.readuntil(b"\r\n\r\n")).decode().split("\r\n")
        lines.append(head)
        writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    proxy = ProxyConfig(f"127.0.0.1:{port}", "http", "user-fr", PASSWORD)
    try:
        with pytest.raises(OutboundProxyError) as failed:
            await HttpWebClient().request(
                "GET", PAGE, headers={}, address=PUBLIC, max_bytes=1000, proxy=proxy
            )
    finally:
        server.close()
    assert lines[0][0] == "CONNECT docs.example.com:443 HTTP/1.1", lines[0][0]
    assert PUBLIC not in "".join(lines[0])
    auth = next(h for h in lines[0] if h.lower().startswith("proxy-authorization"))
    assert base64.b64decode(auth.split()[-1]).decode() == f"user-fr:{PASSWORD}"
    assert f"http://127.0.0.1:{port}" in str(failed.value) and PASSWORD not in str(failed.value)


# ───────────────────────────── web_search ─────────────────────────────


@pytest.fixture
def brave(monkeypatch: pytest.MonkeyPatch, net: Net) -> None:
    """The real Brave backend over the test's direct transport (not the fake the suite switches on)."""
    import choregos_adapters
    from choregos_adapters.web_search import BraveSearch

    def build(kind: str, type_name: str, config: dict[str, Any]) -> Any:
        return BraveSearch(api_key=str(config.get("api_key", "")), transport=net.direct_transport())

    monkeypatch.setattr(choregos_adapters, "build", build)
    monkeypatch.setenv("CONNECTOR_TEST_SEARCH_KEY", "search-key-from-the-vault")


async def _search(client: AsyncClient, base: str, headers: dict[str, str]) -> Any:
    answer = await client.post(f"{base}/web_search", headers=headers, json={"query": QUERY})
    assert answer.status_code == 200, answer.text
    return Envelope(answer.json())


async def test_web_search_goes_through_the_proxy_when_it_applies_and_direct_when_not(
    client: AsyncClient, project: dict[str, Any], brave: None, net: Net
) -> None:
    base, headers = await _search_ready(client, project, ceiling={"mode": "allow"}, handling=ZERO)
    await _put(client, applies_to=["web_fetch"])
    assert (await _search(client, base, headers)).status_code == 200
    assert [r.url.host for r in net.direct] == ["api.search.brave.com"] and net.via_proxy == []

    net.direct.clear()
    await _put(client, applies_to=["web_search"], enabled=False, password=None)
    assert (await _search(client, base, headers)).status_code == 200
    assert len(net.direct) == 1 and net.via_proxy == [], "disabled: direct"

    net.direct.clear()
    await _put(client, applies_to=["web_search"], password=None)
    answer = await _search(client, base, headers)
    assert answer.status_code == 200, answer.text
    assert net.direct == [], "enabled for web_search: no direct call"
    assert [(u, r.url.host) for u, r in net.via_proxy] == [
        (ProxyConfig(BODY["endpoint"], "http", BODY["username"], PASSWORD).url, "api.search.brave.com")
    ]


async def test_web_search_fails_closed_and_names_the_proxy(
    client: AsyncClient, project: dict[str, Any], brave: None, net: Net
) -> None:
    base, headers = await _search_ready(client, project, ceiling={"mode": "allow"}, handling=ZERO)
    await _put(client)
    for mode in ("unreachable", "407"):
        net.proxy_mode = mode
        answer = await _search(client, base, headers)
        assert answer.status_code == 502, answer.text
        assert "http://gate.proxy.example:7000" in answer.text
        assert PASSWORD not in answer.text and ENCODED not in answer.text
    assert net.direct == [], "a failing proxy is never bypassed"


async def test_a_self_hosted_firecrawl_is_not_sent_through_the_proxy(net: Net) -> None:
    from choregos_adapters.web_search import FirecrawlSearch

    assert FirecrawlSearch.PROXIED is False


# ───────────────────────────── the knowledge web crawl ─────────────────────────────


async def _public(host: str) -> list[str]:
    return [PUBLIC]


def _crawl(net: Net) -> Any:
    from choregos_adapters.documents import WebCrawlSource

    return WebCrawlSource(
        url="https://docs.example.com/guide/", transport=net.direct_transport(), resolver=_public
    )


async def test_a_guarded_crawl_reads_the_proxy_of_its_context(net: Net) -> None:
    proxy = ProxyConfig(BODY["endpoint"], "http", BODY["username"], PASSWORD)
    for scope, expected in (
        ({}, "direct"),
        ({"web_fetch": proxy}, "direct"),
        ({"knowledge_web": proxy}, "proxy"),
    ):
        net.direct.clear(), net.via_proxy.clear()
        crawl = _crawl(net)
        with use_proxies(scope):
            await crawl.check_start()
        await crawl.aclose()
        assert (len(net.via_proxy) > 0) == (expected == "proxy"), scope
        assert (len(net.direct) > 0) == (expected == "direct"), scope


async def test_a_crawl_through_a_failing_proxy_fails_and_is_not_retried_directly(net: Net) -> None:
    proxy = ProxyConfig(BODY["endpoint"], "http", BODY["username"], PASSWORD)
    for mode in ("unreachable", "407"):
        net.proxy_mode = mode
        crawl = _crawl(net)
        with (
            use_proxies({"knowledge_web": proxy}),
            pytest.raises(httpx.HTTPError, match=r"gate\.proxy\.example:7000"),
        ):
            await crawl.check_start()
        await crawl.aclose()
    assert net.direct == []


async def test_the_proxy_never_widens_what_the_crawl_may_reach(
    net: Net, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard checks the TARGET before the proxy sees the request (ADR 0087 §3)."""
    from choregos_adapters.http.egress import EgressRefusedHttpError
    from choregos_core.egress import EgressPolicy, use_policy

    proxy = ProxyConfig(BODY["endpoint"], "http", BODY["username"], PASSWORD)
    crawl = _crawl(net)
    policy = EgressPolicy(org="varga", rules=(), requested=(), mode="enforce")
    with (
        use_policy(policy, "api", "test"),
        use_proxies({"knowledge_web": proxy}),
        pytest.raises(EgressRefusedHttpError),
    ):
        await crawl.check_start()
    await crawl.aclose()
    assert net.via_proxy == [] and net.direct == []


async def test_the_knowledge_sync_enters_the_organisations_proxy_scope(
    client: AsyncClient, admin: str, net: Net, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_adapters.documents import WebCrawlSource
    from choregos_api.knowledge.sync import sync_source

    async def open_source(session: Any, source: Any) -> Any:
        return WebCrawlSource(
            url="https://docs.example.test/guide/", transport=net.direct_transport(), resolver=_public
        )

    monkeypatch.setattr("choregos_api.knowledge.sync.open_source", open_source)
    base = "/api/v1/orgs/varga/knowledge"
    await client.post(base, json={"slug": "docs", "name": "Docs", "serving_mode": "index"})
    created = await client.post(
        f"{base}/docs/sources",
        json={
            "kind": "web",
            "name": "Site",
            "config": {"url": "https://docs.example.test/guide/"},
            "allow_egress": True,
        },
    )
    source_id = str(created.json()["id"])
    await _put(client, applies_to=["knowledge_web"])
    await sync_source("varga", source_id)
    assert net.via_proxy, "the sync's crawl went through the proxy"
    assert net.direct == [], "and not around it"
    net.via_proxy.clear()
    await _put(client, applies_to=["web_fetch"], password=None)
    await sync_source("varga", source_id)
    assert net.via_proxy == [] and net.direct, "knowledge_web not in applies_to: direct"
    net.direct.clear()
    await _put(client, applies_to=["knowledge_web"], password=None)
    net.proxy_mode = "unreachable"
    with pytest.raises(httpx.HTTPError, match=r"gate\.proxy\.example:7000"):
        await sync_source("varga", source_id)
    assert net.direct == [], "a failing proxy is never bypassed"
