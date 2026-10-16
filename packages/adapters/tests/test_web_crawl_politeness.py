"""A crawl is polite (ADR 0070): it waits for the site's `Crawl-delay` and the organisation's rate,
stops when the site says 429, and says who it is."""

from __future__ import annotations

import httpx
from choregos_adapters.documents import WebCrawlSource

ROOT = "https://docs.example.com/guide/"
LINKS = "".join(f'<a href="p{i}.html">p{i}</a>' for i in range(1, 4))


async def _public(host: str) -> list[str]:
    return ["93.184.216.34"]


def _site(
    robots: str = "", *, throttle_from: int | None = None, seen: list[httpx.Request] | None = None
) -> httpx.MockTransport:
    served = {"pages": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        url = str(request.url)
        if url.endswith("/robots.txt"):
            return httpx.Response(200, text=robots)
        served["pages"] += 1
        if throttle_from is not None and served["pages"] >= throttle_from:
            return httpx.Response(429, headers={"retry-after": "30"})
        body = LINKS if url == ROOT else "leaf"
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    return httpx.MockTransport(handler)


def _crawl(transport: httpx.MockTransport, sleeps: list[float], **kwargs: object) -> WebCrawlSource:
    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return WebCrawlSource(
        url=ROOT,
        max_depth=1,
        transport=transport,
        resolver=_public,
        sleep=sleep,
        **kwargs,  # type: ignore[arg-type]
    )


async def test_the_crawl_waits_between_pages_for_the_organisations_rate() -> None:
    sleeps: list[float] = []
    changes = await _crawl(_site(), sleeps, per_host_per_minute=30).changes(None)
    assert len(changes.items) == 4
    assert sleeps == [2.0, 2.0, 2.0], "60 / 30 requests a minute, between four pages, none before the first"


async def test_the_crawl_waits_for_the_longer_of_crawl_delay_and_the_rate() -> None:
    sleeps: list[float] = []
    robots = "User-agent: ChoregosKnowledge\nCrawl-delay: 7\n"
    await _crawl(_site(robots), sleeps, per_host_per_minute=60).changes(None)
    assert sleeps == [7.0, 7.0, 7.0]
    sleeps.clear()
    await _crawl(_site(robots), sleeps, per_host_per_minute=6).changes(None)
    assert sleeps == [10.0, 10.0, 10.0], "the organisation's rate is slower than the site asks"


async def test_a_crawl_without_a_rate_or_a_delay_does_not_sleep() -> None:
    sleeps: list[float] = []
    await _crawl(_site(), sleeps).changes(None)
    assert sleeps == []


async def test_a_429_stops_the_crawl_and_forbids_every_deletion() -> None:
    sleeps: list[float] = []
    changes = await _crawl(_site(throttle_from=3), sleeps).changes(None)
    assert [i.ref for i in changes.items] == [ROOT, f"{ROOT}p1.html"], "read before the site asked to stop"
    assert changes.unreadable == [f"{ROOT}p2.html"], "the page that was refused is unread, not deleted"
    assert changes.full, "and `unreadable` is what keeps the sync from deleting what it did not reach"


async def test_the_platforms_user_agent_is_sent_and_the_platforms_robots_token_obeyed() -> None:
    seen: list[httpx.Request] = []
    agent = "Choregos/9.9 (+https://ops.example.com/contact)"
    robots = "User-agent: Choregos\nDisallow: /guide/p2.html\n"
    changes = await _crawl(_site(robots, seen=seen), [], user_agent=agent).changes(None)
    assert {r.headers["user-agent"] for r in seen} == {agent}
    assert f"{ROOT}p2.html" not in [i.ref for i in changes.items]
    assert f"{ROOT}p1.html" in [i.ref for i in changes.items]


async def test_the_default_user_agent_is_unchanged() -> None:
    seen: list[httpx.Request] = []
    await _crawl(_site(seen=seen), []).changes(None)
    assert {r.headers["user-agent"] for r in seen} == {"ChoregosKnowledge/1.0 (+https://choregos.dev)"}
