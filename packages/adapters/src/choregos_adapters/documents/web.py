# SPDX-License-Identifier: Apache-2.0
"""An allow-listed web site, crawled (S33-04).

Same scheme and host as the start URL, under its path prefix; `robots.txt` obeyed for the
`ChoregosKnowledge` agent; depth and page caps; HTML pages only. The host must not resolve to a
private, loopback or link-local address: a knowledge base is not a way into the cluster's network.

A crawl sees the whole site each time (`full=True`): a page it no longer reaches was deleted. A crawl
that lists nothing at all is not such a case (a moved site, a redirect, a wall): it reports its start
URL as unreadable, so it deletes nothing. Redirects are followed only inside the allow-list; `exclude`
holds path globs that are neither fetched nor followed.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from fnmatch import fnmatchcase
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from ..http.egress import guarded
from .base import Changes, SourceItem, SourceRefusedError

USER_AGENT = "ChoregosKnowledge/1.0 (+https://choregos.dev)"
#: The names a site's `robots.txt` may address the crawler by: its historical one, and the platform's
#: (ADR 0070). A page either of them is refused is not read; the longer `Crawl-delay` wins.
ROBOTS_TOKENS = ("ChoregosKnowledge", "Choregos")
MAX_DEPTH = 5
MAX_PAGES = 500
MAX_PAGE_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5

Resolver = Callable[[str], Awaitable[list[str]]]
Sleep = Callable[[float], Awaitable[None]]


async def resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def _private(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast


def page_name(url: str) -> str:
    """A page is named by its whole path, so `/en/about` and `/fr/about` do not collide."""
    name = "-".join(part for part in urlsplit(url).path.split("/") if part) or "index"
    return name if name.endswith((".html", ".htm")) else f"{name}.html"


#: Public name of the predicate: `web_fetch` (S40-01f) refuses the same addresses a crawl does.
is_private_address = _private


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)


class WebCrawlSource:
    def __init__(  # noqa: PLR0913 - one crawler, each knob a keyword
        self,
        *,
        url: str,
        max_depth: int = 2,
        max_pages: int = 50,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver = resolve,
        allow_http: bool = False,
        exclude: list[str] | None = None,
        user_agent: str = USER_AGENT,
        per_host_per_minute: int | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        parts = urlsplit(url)
        if parts.scheme != "https" and not (allow_http and parts.scheme == "http"):
            raise SourceRefusedError(f"a crawled site is https: `{url}`")
        if not parts.hostname:
            raise SourceRefusedError(f"no host in `{url}`")
        self.start = urldefrag(url)[0]
        self.origin = f"{parts.scheme}://{parts.netloc}"
        self.prefix = parts.path.rsplit("/", 1)[0] + "/" if parts.path else "/"
        self.host = parts.hostname
        self.exclude = list(exclude or [])
        self.max_depth = max(0, min(max_depth, MAX_DEPTH))
        self.max_pages = max(1, min(max_pages, MAX_PAGES))
        self._resolver = resolver
        self.user_agent = user_agent
        #: The wait between two page fetches that the organisation's rate imposes (ADR 0070).
        self._rate_delay = 60.0 / per_host_per_minute if per_host_per_minute else 0.0
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            timeout=20.0,
            transport=guarded(transport),
            follow_redirects=False,
            headers={"User-Agent": user_agent},
        )
        self._pages: dict[str, bytes] = {}

    async def _check_host(self) -> None:
        try:
            addresses = await self._resolver(self.host)
        except OSError as exc:
            raise SourceRefusedError(f"`{self.host}` does not resolve: {exc}") from exc
        if not addresses or any(_private(a) for a in addresses):
            raise SourceRefusedError(f"`{self.host}` resolves to a private address: not crawled")

    def _inside(self, url: str) -> bool:
        parts = urlsplit(url)
        path = parts.path or "/"
        return (
            f"{parts.scheme}://{parts.netloc}" == self.origin
            and path.startswith(self.prefix)
            and not any(fnmatchcase(path, pattern) for pattern in self.exclude)
        )

    async def _get(self, url: str, robots: RobotFileParser | None = None) -> tuple[str, httpx.Response]:
        """One GET, following redirects that stay inside the allow-list. Anything else is returned as is."""
        for _ in range(MAX_REDIRECTS + 1):
            response = await self._http.get(url)
            location = response.headers.get("location")
            if not (300 <= response.status_code < 400 and location):
                return url, response
            target = urldefrag(urljoin(url, location))[0]
            if not self._inside(target) or (robots is not None and not robots.can_fetch(USER_AGENT, target)):
                return url, response
            url = target
        return url, response

    async def aclose(self) -> None:
        await self._http.aclose()

    async def check_start(self) -> None:
        """Refuses, with its reason, a start URL that leaves the allow-list (`www` to apex, another host)."""
        await self._check_host()
        url, response = await self._get(self.start)
        location = response.headers.get("location")
        if 300 <= response.status_code < 400 and location:
            raise SourceRefusedError(f"`{self.start}` redirects to `{urljoin(url, location)}`: start there")

    async def _robots(self) -> RobotFileParser:
        robots = RobotFileParser()
        response = await self._http.get(f"{self.origin}/robots.txt")
        robots.parse(response.text.splitlines() if response.status_code == 200 else [])
        return robots

    @staticmethod
    def _can_fetch(robots: RobotFileParser, url: str) -> bool:
        return all(robots.can_fetch(token, url) for token in ROBOTS_TOKENS)

    def _delay(self, robots: RobotFileParser) -> float:
        """Seconds to wait between two pages: the site's `Crawl-delay` or the organisation's rate,
        whichever is longer."""
        asked = max((float(robots.crawl_delay(token) or 0) for token in ROBOTS_TOKENS), default=0.0)
        return max(asked, self._rate_delay)

    async def changes(self, cursor: dict[str, Any] | None) -> Changes:
        await self._check_host()
        robots = await self._robots()
        delay = self._delay(robots)
        seen: set[str] = set()
        frontier = [(self.start, 0)]
        items: list[SourceItem] = []
        unreadable: list[str] = []
        while frontier and len(items) < self.max_pages:
            url, depth = frontier.pop(0)
            if url in seen or not self._inside(url) or not self._can_fetch(robots, url):
                continue
            if seen and delay:
                await self._sleep(delay)
            seen.add(url)
            try:
                final, response = await self._get(url, robots)
            except httpx.HTTPError:
                unreadable.append(url)
                continue
            if response.status_code == 429 or (
                response.status_code == 503 and "retry-after" in response.headers
            ):
                # The site asks us to slow down: stop here, whatever is unread keeps its place
                # (an `unreadable` page forbids every deletion of this pass) and the next pass retries.
                unreadable.append(url)
                break
            if response.status_code >= 500:
                # A server error is not a deletion: the page keeps its place until it reads again.
                unreadable.append(url)
                continue
            kind = response.headers.get("content-type", "")
            if response.status_code != 200 or "html" not in kind or len(response.content) > MAX_PAGE_BYTES:
                continue
            if final != url:
                if final in seen:
                    continue
                seen.add(final)
                url = final
            name = page_name(url)
            self._pages[url] = response.content
            items.append(
                SourceItem(ref=url, name=name, version=hashlib.sha256(response.content).hexdigest(), url=url)
            )
            if depth < self.max_depth:
                parser = _Links()
                parser.feed(response.text)
                for link in parser.links:
                    target = urldefrag(urljoin(url, link))[0]
                    if target not in seen:
                        frontier.append((target, depth + 1))
        if not items:
            # Nothing listed is never "everything was deleted": the start moved, or the site is walled.
            return Changes(items=[], full=True, cursor=cursor, unreadable=[self.start])
        return Changes(items=items, full=True, cursor={"pages": len(items)}, unreadable=unreadable)

    async def fetch(self, item: SourceItem) -> bytes:
        if item.ref in self._pages:
            return self._pages[item.ref]
        _, response = await self._get(item.ref)
        response.raise_for_status()
        return response.content
