# SPDX-License-Identifier: Apache-2.0
"""An allow-listed web site, crawled (S33-04).

Same scheme and host as the start URL, under its path prefix; `robots.txt` obeyed for the
`ChoregosKnowledge` agent; depth and page caps; HTML pages only. The host must not resolve to a
private, loopback or link-local address: a knowledge base is not a way into the cluster's network.

A crawl sees the whole site each time (`full=True`): a page it no longer reaches was deleted. A crawl
that lists nothing at all is not such a case (a moved site, a redirect, a wall): it reports its start
URL as unreadable, so it deletes nothing. Redirects are followed only inside the allow-list; `exclude`
holds path globs that are neither fetched nor followed.

A site behind a login is read with the headers of a `documents/web` connector (S47-48): a bearer
token, HTTP Basic, or one named header (an API key, a session cookie). The credential is pinned to the
connector's host, and every request of a crawl stays on its start origin, so it is never sent
elsewhere. A source may also carry plain headers of its own (`Accept-Language`); a credential never
goes there.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import re
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


#: The documents a crawl may list besides HTML pages (`include_documents`): content type to extension.
DOCUMENT_TYPES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}
DOCUMENT_EXTENSIONS = tuple(DOCUMENT_TYPES.values())


def _document_extension(url: str, content_type: str) -> str | None:
    """The extension of a linked document, from its content type or, failing that, its URL path."""
    kind = content_type.split(";", 1)[0].strip().lower()
    if kind in DOCUMENT_TYPES:
        return DOCUMENT_TYPES[kind]
    path = urlsplit(url).path.lower()
    return next((ext for ext in DOCUMENT_EXTENSIONS if path.endswith(ext)), None)


def page_name(url: str, extension: str | None = None) -> str:
    """A page is named by its whole path, so `/en/about` and `/fr/about` do not collide.

    A linked document keeps its real extension (`extension`, from its content type when the URL has none)."""
    name = "-".join(part for part in urlsplit(url).path.split("/") if part) or "index"
    if name.lower().endswith(DOCUMENT_EXTENSIONS):
        return name
    if extension:
        return f"{name}{extension}"
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
        include_documents: bool = False,
        user_agent: str = USER_AGENT,
        per_host_per_minute: int | None = None,
        sleep: Sleep = asyncio.sleep,
        headers: dict[str, str] | None = None,
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
        self.include_documents = include_documents
        self.max_depth = max(0, min(max_depth, MAX_DEPTH))
        self.max_pages = max(1, min(max_pages, MAX_PAGES))
        self._resolver = resolver
        self.user_agent = user_agent
        #: The wait between two page fetches that the organisation's rate imposes (ADR 0070).
        self._rate_delay = 60.0 / per_host_per_minute if per_host_per_minute else 0.0
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            timeout=20.0,
            transport=guarded(transport, proxy_kind="knowledge_web"),
            follow_redirects=False,
            headers={**checked_headers(headers or {}), "User-Agent": user_agent},
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
            parts = urlsplit(url)
            if f"{parts.scheme}://{parts.netloc}" != self.origin:
                # The client carries the source's headers, a credential among them: never off the origin.
                raise SourceRefusedError(f"`{url}` is not on `{self.origin}`")
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
            if response.status_code != 200 or len(response.content) > MAX_PAGE_BYTES:
                continue
            is_html = "html" in kind
            extension = (
                None if is_html else _document_extension(final, kind) if self.include_documents else None
            )
            if not is_html and extension is None:
                continue
            if final != url:
                if final in seen:
                    continue
                seen.add(final)
                url = final
            name = page_name(url, extension)
            self._pages[url] = response.content
            items.append(
                SourceItem(ref=url, name=name, version=hashlib.sha256(response.content).hexdigest(), url=url)
            )
            if is_html and depth < self.max_depth:
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


#: An HTTP header name (RFC 9110 `token`).
_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]{1,100}$")
#: Headers the crawl sets itself, or that would change where it goes.
RESERVED_HEADERS = frozenset({"host", "user-agent", "content-length", "transfer-encoding", "connection"})
#: The names a credential travels under: refused as a source's plain header, they belong in a connector.
_CREDENTIAL_NAME = re.compile(r"auth|cookie|token|key|secret|session|password|signature", re.IGNORECASE)
MAX_HEADERS = 20
MAX_HEADER_VALUE = 4096


def checked_headers(headers: dict[str, str]) -> dict[str, str]:
    """Headers a crawl may send: well-formed names, values on one line, none the crawl sets itself."""
    if len(headers) > MAX_HEADERS:
        raise SourceRefusedError(f"at most {MAX_HEADERS} headers")
    for name, value in headers.items():
        if not isinstance(name, str) or not _HEADER_NAME.match(name):
            raise SourceRefusedError(f"not a header name: `{name}`")
        if name.lower() in RESERVED_HEADERS:
            raise SourceRefusedError(f"the crawl sets `{name}` itself")
        if not isinstance(value, str) or len(value) > MAX_HEADER_VALUE or any(c in value for c in "\r\n\0"):
            raise SourceRefusedError(
                f"the value of `{name}` is one line of at most {MAX_HEADER_VALUE} characters"
            )
    return dict(headers)


def plain_headers(headers: dict[str, str]) -> dict[str, str]:
    """A source's own headers: no credential among them, it would be stored in the clear."""
    checked = checked_headers(headers)
    for name in checked:
        if _CREDENTIAL_NAME.search(name):
            raise SourceRefusedError(
                f"`{name}` looks like a credential: it goes in a `documents/web` connector, "
                "where it is a secret"
            )
    return checked


_HOST = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


def check_connector_config(config: dict[str, Any]) -> None:
    """A `documents/web` connector's configuration, refused when saved rather than at its first sync."""
    host = str(config.get("host") or "").strip().lower()
    if not _HOST.match(host):
        raise SourceRefusedError(f"`host` is a host name, `docs.example.com`, not `{host}`")
    auth = str(config.get("auth") or "bearer")
    if auth not in {"bearer", "basic", "header"}:
        raise SourceRefusedError(f"unknown web auth `{auth}`: `bearer`, `basic` or `header`")
    if auth == "basic" and not str(config.get("username") or ""):
        raise SourceRefusedError("an `auth: basic` connector has a `username`")
    if auth == "header":
        name = str(config.get("header_name") or "")
        if not _HEADER_NAME.match(name) or name.lower() in RESERVED_HEADERS:
            raise SourceRefusedError(f"`header_name` is a header the secret goes in, not `{name}`")
    extra = config.get("headers") or {}
    if not isinstance(extra, dict):
        raise SourceRefusedError("the connector's `headers` map names to values")
    checked_headers(extra)


class WebDocumentsConnector:
    """A `documents/web` connector: the credential of a site behind a login, pinned to its host.

    `auth: bearer` sends `Authorization: Bearer <secret>`; `auth: basic` sends HTTP Basic with
    `username` and the secret as the password; `auth: header` sends the secret in `header_name` (an
    `X-API-Key`, a `Cookie`). `headers` are plain headers sent with it. A source on another host is
    refused, so the credential is never sent elsewhere.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.host = str(config.get("host") or "").strip().lower()
        self.auth = str(config.get("auth") or "bearer")

    def headers(self) -> dict[str, str]:
        secret = str(self.config.get("secret") or "")
        if not secret:
            raise SourceRefusedError("the `documents/web` connector has no `secret`")
        extra = self.config.get("headers") or {}
        if not isinstance(extra, dict):
            raise SourceRefusedError("the connector's `headers` map names to values")
        out = checked_headers({str(k): str(v) for k, v in extra.items()})
        if self.auth == "bearer":
            out["Authorization"] = f"Bearer {secret}"
        elif self.auth == "basic":
            user = str(self.config.get("username") or "")
            if not user:
                raise SourceRefusedError("an `auth: basic` connector has a `username`")
            out["Authorization"] = "Basic " + base64.b64encode(f"{user}:{secret}".encode()).decode()
        elif self.auth == "header":
            name = str(self.config.get("header_name") or "")
            if not name:
                raise SourceRefusedError("an `auth: header` connector names its `header_name`")
            out[name] = secret
        else:
            raise SourceRefusedError(f"unknown web auth `{self.auth}`: `bearer`, `basic` or `header`")
        return checked_headers(out)

    def source(
        self,
        config: dict[str, Any],
        *,
        key: str,
        user_agent: str = USER_AGENT,
        per_host_per_minute: int | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver = resolve,
    ) -> WebCrawlSource:
        url = str(config["url"])
        check_connector_config(self.config)
        if (urlsplit(url).hostname or "").lower() != self.host:
            raise SourceRefusedError(
                f"`{url}` is not on `{self.host}`: the connector's credential is never sent to another host"
            )
        own = config.get("headers") or {}
        return WebCrawlSource(
            url=url,
            max_depth=int(config.get("max_depth", 2)),
            max_pages=int(config.get("max_pages", 50)),
            exclude=config.get("exclude"),
            include_documents=config.get("include_documents") is True,
            user_agent=user_agent,
            per_host_per_minute=per_host_per_minute,
            transport=transport,
            resolver=resolver,
            headers={**plain_headers(dict(own)), **self.headers()},
        )
