# SPDX-License-Identifier: Apache-2.0
"""An allow-listed web site, crawled (S33-04).

Same scheme and host as the start URL, under its path prefix; `robots.txt` obeyed for the
`ChoregosKnowledge` agent; depth and page caps; HTML pages only. The host must not resolve to a
private, loopback or link-local address: a knowledge base is not a way into the cluster's network.

A crawl sees the whole site each time (`full=True`): a page it no longer reaches was deleted.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from .base import Changes, SourceItem, SourceRefusedError

USER_AGENT = "ChoregosKnowledge/1.0 (+https://choregos.dev)"
MAX_DEPTH = 5
MAX_PAGES = 500
MAX_PAGE_BYTES = 5 * 1024 * 1024

Resolver = Callable[[str], Awaitable[list[str]]]


async def resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def _private(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast


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
    def __init__(
        self,
        *,
        url: str,
        max_depth: int = 2,
        max_pages: int = 50,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver = resolve,
        allow_http: bool = False,
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
        self.max_depth = max(0, min(max_depth, MAX_DEPTH))
        self.max_pages = max(1, min(max_pages, MAX_PAGES))
        self._resolver = resolver
        self._http = httpx.AsyncClient(
            timeout=20.0, transport=transport, follow_redirects=False, headers={"User-Agent": USER_AGENT}
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
        return f"{parts.scheme}://{parts.netloc}" == self.origin and (parts.path or "/").startswith(
            self.prefix
        )

    async def _robots(self) -> RobotFileParser:
        robots = RobotFileParser()
        response = await self._http.get(f"{self.origin}/robots.txt")
        robots.parse(response.text.splitlines() if response.status_code == 200 else [])
        return robots

    async def changes(self, cursor: dict[str, Any] | None) -> Changes:
        await self._check_host()
        robots = await self._robots()
        seen: set[str] = set()
        frontier = [(self.start, 0)]
        items: list[SourceItem] = []
        unreadable: list[str] = []
        while frontier and len(items) < self.max_pages:
            url, depth = frontier.pop(0)
            if url in seen or not self._inside(url) or not robots.can_fetch(USER_AGENT, url):
                continue
            seen.add(url)
            try:
                response = await self._http.get(url)
            except httpx.HTTPError:
                unreadable.append(url)
                continue
            if response.status_code >= 500 or response.status_code == 429:
                # A server error is not a deletion: the page keeps its place until it reads again.
                unreadable.append(url)
                continue
            kind = response.headers.get("content-type", "")
            if response.status_code != 200 or "html" not in kind or len(response.content) > MAX_PAGE_BYTES:
                continue
            self._pages[url] = response.content
            path = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1] or "index"
            name = path if path.endswith((".html", ".htm")) else f"{path}.html"
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
        return Changes(items=items, full=True, cursor={"pages": len(items)}, unreadable=unreadable)

    async def fetch(self, item: SourceItem) -> bytes:
        if item.ref in self._pages:
            return self._pages[item.ref]
        response = await self._http.get(item.ref)
        response.raise_for_status()
        return response.content
