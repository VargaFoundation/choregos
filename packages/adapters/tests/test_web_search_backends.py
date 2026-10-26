"""The `web_search` backends (S40-01g, ADR 0083): what each sends, what it keeps of the answer, how it
fails. The key goes in a header and never appears in a result or an error; a 429 carries its wait;
no backend asks its provider to fetch pages."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from choregos_adapters.web_search import (
    BACKENDS,
    BraveSearch,
    FirecrawlSearch,
    SearchError,
    SearchRateLimited,
    TavilySearch,
    declared,
)
from choregos_core.web_search import DataHandling

KEY = "search-key-under-test"


def _wire(status: int, body: Any, seen: list[httpx.Request], headers: dict[str, str] | None = None) -> Any:
    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body, headers=headers or {})

    return httpx.MockTransport(answer)


async def test_brave_sends_the_key_in_a_header_and_maps_the_filters() -> None:
    seen: list[httpx.Request] = []
    first = {
        "title": "A",
        "url": "https://docs.python.org/a",
        "description": "about  a",
        "page_age": "2026-01-01",
    }
    hits = {
        "web": {"results": [first, {"title": "no link", "url": "javascript:alert(1)", "description": "x"}]}
    }
    brave = BraveSearch(api_key=KEY, transport=_wire(200, hits, seen))
    found = await brave.search("asyncio", max_results=50, site="docs.python.org", recency="week")
    assert found == [
        {"title": "A", "url": "https://docs.python.org/a", "snippet": "about a", "published_at": "2026-01-01"}
    ]
    sent = seen[0]
    assert sent.method == "GET" and sent.url.host == "api.search.brave.com"
    assert sent.headers["X-Subscription-Token"] == KEY
    assert sent.url.params["q"] == "asyncio site:docs.python.org"
    assert sent.url.params["count"] == "10", "capped whatever is asked"
    assert sent.url.params["freshness"] == "pw"
    assert KEY not in str(sent.url)


async def test_tavily_never_asks_for_pages() -> None:
    seen: list[httpx.Request] = []
    body = {
        "results": [{"title": "T", "url": "https://t.example.org/", "content": "c", "published_date": None}]
    }
    tavily = TavilySearch(api_key=KEY, transport=_wire(200, body, seen))
    found = await tavily.search("q", max_results=3, site="t.example.org", recency="day")
    assert found == [{"title": "T", "url": "https://t.example.org/", "snippet": "c"}]
    sent = json.loads(seen[0].content)
    assert sent["include_raw_content"] is False, "reading stays with web_fetch"
    assert sent["include_domains"] == ["t.example.org"] and sent["time_range"] == "day"
    assert sent["max_results"] == 3
    assert seen[0].headers["Authorization"] == f"Bearer {KEY}"
    assert KEY not in seen[0].content.decode()


async def test_firecrawl_never_scrapes_and_runs_self_hosted_without_a_key() -> None:
    seen: list[httpx.Request] = []
    body = {"data": [{"title": "F", "url": "https://f.example.org/", "description": "d"}]}
    firecrawl = FirecrawlSearch(base_url="http://firecrawl.internal:3002/", transport=_wire(200, body, seen))
    found = await firecrawl.search("q", recency="month")
    assert found == [{"title": "F", "url": "https://f.example.org/", "snippet": "d"}]
    assert str(seen[0].url) == "http://firecrawl.internal:3002/v1/search"
    sent = json.loads(seen[0].content)
    assert "scrapeOptions" not in sent, "the provider does not fetch pages for us"
    assert sent["tbs"] == "qdr:m"
    assert "Authorization" not in seen[0].headers, "a self-hosted instance may run without a key"


@pytest.mark.parametrize(
    "cloud",
    [
        "https://api.firecrawl.dev",
        "https://firecrawl.dev/",
        "https://API.Firecrawl.Dev./v1",
        "https://eu.api.firecrawl.dev:443",
        "http://user:pw@api.firecrawl.dev",
    ],
)
def test_firecrawl_refuses_the_cloud_in_the_adapter(cloud: str) -> None:
    """S40-01k, defence in depth: even a row written behind the API's check never reaches the cloud."""
    with pytest.raises(SearchError, match=r"cloud.*self-hosted"):
        FirecrawlSearch(base_url=cloud)


@pytest.mark.parametrize("near", ["http://firecrawl.dev.internal:3002", "https://notfirecrawl.dev"])
def test_firecrawl_refuses_only_the_cloud_domain_not_lookalikes(near: str) -> None:
    FirecrawlSearch(base_url=near)


@pytest.mark.parametrize("bad", ["", "   ", "firecrawl.internal", "ftp://firecrawl.internal"])
def test_firecrawl_without_a_usable_base_url_is_refused_not_defaulted(bad: str) -> None:
    with pytest.raises(SearchError, match="base_url"):
        FirecrawlSearch(base_url=bad)


def test_a_stored_connector_without_base_url_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Connectors saved when the cloud was the default carry no `base_url`: built through the registry
    they raise an explicit error, before any client exists, so nothing can be sent."""
    from choregos_adapters import build

    monkeypatch.setattr("choregos_adapters.registry.fakes_enabled", lambda: False)  # the real type

    with pytest.raises(SearchError, match="self-hosted"):
        build("web_search", "firecrawl", {"api_key": "k", "timeout_s": 5})


def test_the_connector_type_requires_base_url_and_declares_the_self_hosted_host_as_egress() -> None:
    from choregos_adapters import egress_hosts, spec_of, validate_config

    spec = spec_of("web_search", "firecrawl")
    assert spec is not None
    assert spec.config_schema["required"] == ["base_url"]
    assert "default" not in spec.config_schema["properties"]["base_url"], "no cloud default"
    assert egress_hosts("web_search", "firecrawl", {"base_url": "http://firecrawl.internal:3002"}) == [
        ("firecrawl.internal", 3002)
    ]
    assert egress_hosts("web_search", "firecrawl", {"base_url": "https://fc.corp.example/"}) == [
        ("fc.corp.example", 443)
    ]
    assert egress_hosts("web_search", "firecrawl", {}) == [], "never the cloud's host by default"
    validate_config("web_search", "firecrawl", {"base_url": "http://firecrawl.internal:3002"})
    for bad in ({}, {"base_url": "https://api.firecrawl.dev"}):
        with pytest.raises(SearchError):
            validate_config("web_search", "firecrawl", bad)


async def test_a_429_carries_its_wait_and_no_retry_is_made() -> None:
    seen: list[httpx.Request] = []
    brave = BraveSearch(api_key=KEY, transport=_wire(429, {}, seen, {"retry-after": "12"}))
    with pytest.raises(SearchRateLimited) as limited:
        await brave.search("q")
    assert limited.value.retry_after_s == 12
    assert len(seen) == 1, "a retry storm burns a quota"


@pytest.mark.parametrize("status", [401, 403, 500])
async def test_a_failure_never_shows_the_key(status: int) -> None:
    tavily = TavilySearch(api_key=KEY, transport=_wire(status, {"error": f"bad key {KEY}"}, []))
    with pytest.raises(SearchError) as failed:
        await tavily.search("q")
    assert KEY not in str(failed.value)
    assert KEY not in repr(tavily)


async def test_a_network_error_names_its_class_not_its_url() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}")

    brave = BraveSearch(api_key=KEY, transport=httpx.MockTransport(down))
    with pytest.raises(SearchError) as failed:
        await brave.search("secret-looking query")
    assert "ConnectError" in str(failed.value) and "secret-looking" not in str(failed.value)


async def test_inputs_are_checked_before_anything_is_sent() -> None:
    seen: list[httpx.Request] = []
    brave = BraveSearch(api_key=KEY, transport=_wire(200, {}, seen))
    with pytest.raises(SearchError, match="empty"):
        await brave.search("   ")
    with pytest.raises(SearchError, match="host name"):
        await brave.search("q", site="docs.python.org OR evil.example")
    with pytest.raises(SearchError, match="no api_key"):
        await BraveSearch(transport=_wire(200, {}, seen)).search("q")
    assert seen == []


def test_the_real_backends_declare_nothing_they_have_not_reviewed() -> None:
    """Their terms were not reviewed: every field unknown, so an organisation must state its contract."""
    for name in BACKENDS:
        assert declared(name) == DataHandling(), name
    assert declared("fake").query_retention_days == 0 and declared("fake").query_reuse is False
    assert declared("unheard-of") == DataHandling()


async def test_the_fake_is_deterministic_and_scriptable() -> None:
    from choregos_adapters.fakes.web_search import FakeSearchBackend

    fake = FakeSearchBackend()
    first = await fake.search("q", max_results=2)
    assert first == await fake.search("q", max_results=2) and len(first) == 2
    assert all(r["url"].startswith("https://docs.example.org/") for r in first)
    assert (await fake.search("q", site="peps.python.org"))[0]["url"].startswith("https://peps.python.org/")
    fake.rate_limited = True
    with pytest.raises(SearchRateLimited):
        await fake.search("q")
    fake.reset()
    assert fake.calls == [] and not fake.rate_limited
