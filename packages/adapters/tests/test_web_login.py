"""A web site behind a login (S47-48): the headers of a `documents/web` connector, pinned to its host."""

from __future__ import annotations

import base64

import httpx
import pytest
from choregos_adapters import registry
from choregos_adapters.documents.base import SourceRefusedError
from choregos_adapters.documents.web import (
    WebCrawlSource,
    WebDocumentsConnector,
    check_connector_config,
    plain_headers,
)
from choregos_adapters.errors import ConfigurationError

ROOT = "https://intranet.example.com/docs/"


async def _public(host: str) -> list[str]:
    return ["93.184.216.34"]


def _walled(seen: list[httpx.Request], *, accepts: str) -> httpx.MockTransport:
    """A site that answers its pages only to `accepts` in `Authorization`, and sends others to its login."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.headers.get("authorization") != accepts:
            return httpx.Response(302, headers={"location": "https://login.example.com/"})
        body = '<a href="a.html">a</a><a href="https://elsewhere.example.org/">out</a>'
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    return httpx.MockTransport(handler)


async def test_a_bearer_connector_reads_the_walled_site_and_sends_its_token_nowhere_else() -> None:
    seen: list[httpx.Request] = []
    connector = WebDocumentsConnector({"host": "intranet.example.com", "auth": "bearer", "secret": "t0k"})
    crawl = connector.source(
        {"url": ROOT, "headers": {"Accept-Language": "fr"}},
        key="src-1",
        transport=_walled(seen, accepts="Bearer t0k"),
        resolver=_public,
    )
    changes = await crawl.changes(None)
    assert [i.ref for i in changes.items] == [ROOT, f"{ROOT}a.html"]
    assert {r.url.host for r in seen} == {"intranet.example.com"}
    assert all(r.headers["accept-language"] == "fr" for r in seen)


async def test_without_the_credential_the_same_site_lists_nothing_and_deletes_nothing() -> None:
    seen: list[httpx.Request] = []
    crawl = WebCrawlSource(url=ROOT, transport=_walled(seen, accepts="Bearer t0k"), resolver=_public)
    changes = await crawl.changes(None)
    assert changes.items == [] and changes.unreadable == [ROOT]


@pytest.mark.parametrize(
    ("config", "name", "value"),
    [
        (
            {"auth": "basic", "username": "reader", "secret": "pw"},
            "Authorization",
            "Basic " + base64.b64encode(b"reader:pw").decode(),
        ),
        ({"auth": "header", "header_name": "X-API-Key", "secret": "k"}, "X-API-Key", "k"),
        ({"auth": "header", "header_name": "Cookie", "secret": "sid=1"}, "Cookie", "sid=1"),
    ],
)
def test_basic_and_named_header_auth(config: dict[str, str], name: str, value: str) -> None:
    headers = WebDocumentsConnector({"host": "intranet.example.com", **config}).headers()
    assert headers[name] == value


def test_the_credential_is_pinned_to_the_connector_host() -> None:
    connector = WebDocumentsConnector({"host": "intranet.example.com", "secret": "t0k"})
    with pytest.raises(SourceRefusedError, match="never sent to another host"):
        connector.source({"url": "https://evil.example.org/"}, key="src-1")


async def test_a_crawl_never_requests_off_its_origin() -> None:
    crawl = WebCrawlSource(url=ROOT, headers={"X-Env": "prod"}, resolver=_public)
    with pytest.raises(SourceRefusedError, match="is not on"):
        await crawl._get("https://elsewhere.example.org/")
    await crawl.aclose()


def test_a_credential_is_refused_as_a_plain_header() -> None:
    assert plain_headers({"Accept-Language": "fr"}) == {"Accept-Language": "fr"}
    for name in ("Authorization", "Cookie", "X-API-Key", "X-Auth-Token", "Proxy-Authorization"):
        with pytest.raises(SourceRefusedError, match="looks like a credential"):
            plain_headers({name: "x"})


@pytest.mark.parametrize(
    ("headers", "reason"),
    [
        ({"Bad Name": "x"}, "not a header name"),
        ({"X-Ok": "a\r\nInjected: 1"}, "one line"),
        ({"Host": "elsewhere"}, "sets `Host` itself"),
        ({f"X-{i}": "v" for i in range(21)}, "at most 20"),
    ],
)
def test_malformed_headers_are_refused(headers: dict[str, str], reason: str) -> None:
    with pytest.raises(SourceRefusedError, match=reason):
        WebCrawlSource(url=ROOT, headers=headers)


@pytest.mark.parametrize(
    ("config", "reason"),
    [
        ({"host": "https://intranet.example.com"}, "host name"),
        ({"host": "intranet.example.com", "auth": "digest"}, "unknown web auth"),
        ({"host": "intranet.example.com", "auth": "basic"}, "username"),
        ({"host": "intranet.example.com", "auth": "header"}, "header_name"),
        ({"host": "intranet.example.com", "auth": "header", "header_name": "Host"}, "header_name"),
    ],
)
def test_a_connector_configuration_is_refused_when_saved(config: dict[str, str], reason: str) -> None:
    with pytest.raises(SourceRefusedError, match=reason):
        check_connector_config(config)
    spec = registry.spec_of("documents", "web")
    assert spec is not None and spec.check_config is not None and spec.secret_fields == ("secret",)
    with pytest.raises(ConfigurationError, match=reason):
        spec.check_config(config)


def test_the_connector_without_its_secret_is_refused() -> None:
    with pytest.raises(SourceRefusedError, match="no `secret`"):
        WebDocumentsConnector({"host": "intranet.example.com"}).headers()
