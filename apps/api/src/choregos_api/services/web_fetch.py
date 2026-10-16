# SPDX-License-Identifier: Apache-2.0
"""`web_fetch`: the governed way an agent reads the web (S40-01f, ADR 0070).

An agent that browses from its pod reaches a host the platform cannot see inside TLS: no method, no
path, no robots.txt. This built-in tool is the one place where all of that can hold, because the API
makes the request:

- GET or HEAD only, `https` (or `http` on the default port when the host is allowed there), no port, no
  user-info;
- the host must be in the run's effective network (agent ∩ project ∩ organisation, plus the repository
  host) and not denied; an IP literal or a name that resolves to a private address is refused, and the
  connection is pinned to the address that was checked;
- `robots.txt` of each origin, read for the `Choregos` token and cached for an hour; `Crawl-delay` and
  the effective per-host rate (counted organisation-wide from the cost ledger) become a 429 with a
  `retry_after_s`; the run's own rate too;
- `User-Agent: Choregos/<version> (+<contact>)`;
- at most three redirects, each target going through every check above;
- the answer is capped (2 MiB) and only text, HTML (as text), JSON and markdown come back;
- what comes back is third-party text: it goes through the injection guard like a connector's answer.

The network is behind `WebClient`; `FakeWebClient` serves scripted pages to the tests and to
`CHOREGOS_FAKES=1` (ADR 0005).
"""

from __future__ import annotations

import asyncio
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from html.parser import HTMLParser
from typing import Any, ClassVar, Protocol
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from choregos_adapters.documents.web import is_private_address
from choregos_core import utcnow
from choregos_core.network import allowed
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import CostLedger, Project, Run
from .network import run_network
from .platform_network import calls_to_host, journal, user_agent

TOOL_NAME = "web_fetch"
ROBOTS_TOKEN = "Choregos"  # noqa: S105 - the user-agent token robots.txt addresses, not a secret
MAX_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
ROBOTS_TTL_S = 3600.0
TIMEOUT_S = 20.0
TEXT_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "text/markdown",
    "text/x-markdown",
    "application/json",
)

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "description": "The page to read: https://host/path"},
        "method": {"type": "string", "enum": ["GET", "HEAD"], "description": "GET (default) or HEAD"},
        "max_bytes": {"type": "integer", "minimum": 1, "maximum": MAX_BYTES},
    },
    "required": ["url"],
    "additionalProperties": False,
}


def announcement() -> dict[str, Any]:
    """The tool as the MCP sidecar serves it to the agent."""
    return {
        "name": TOOL_NAME,
        "description": (
            "Read a web page (GET or HEAD) through the platform. Only the hosts this run's network allows; "
            "robots.txt is obeyed and a host is never called faster than its rate: when told to wait, wait. "
            "Text, HTML (returned as text), JSON and markdown only, at most 2 MiB."
        ),
        "inputSchema": INPUT_SCHEMA,
    }


async def offered(session: AsyncSession, run: Run) -> bool:
    """Whether the run's effective network allows at least one host: otherwise there is nothing to read.
    The repository host the platform implies is there so that git works, not so that an agent browses:
    on its own it does not offer the tool (a run it can still read once something else is allowed)."""
    network = await run_network(session, run)
    return bool(network.allow)


# ───────────────────────────── the network ─────────────────────────────


@dataclass
class WebResponse:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    #: the body was cut at `max_bytes`
    truncated: bool = False


class WebClient(Protocol):
    async def request(
        self, method: str, url: str, *, headers: dict[str, str], address: str, max_bytes: int
    ) -> WebResponse:
        """One request, connected to `address` (already checked), the body read up to `max_bytes`."""
        ...


Resolver = Callable[[str], Awaitable[list[str]]]


async def system_resolver(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


class HttpWebClient:
    """httpx, connected to the address that was checked: the URL carries the address, the `Host`
    header and the TLS server name carry the host, so a second DNS answer cannot redirect the call."""

    def __init__(
        self, timeout_s: float = TIMEOUT_S, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.timeout_s = timeout_s
        self.transport = transport

    async def request(
        self, method: str, url: str, *, headers: dict[str, str], address: str, max_bytes: int
    ) -> WebResponse:
        parts = urlsplit(url)
        host = parts.hostname or ""
        literal = f"[{address}]" if ":" in address else address
        port = f":{parts.port}" if parts.port else ""
        pinned = parts._replace(netloc=f"{literal}{port}").geturl()
        sent = {**headers, "Host": host + port}
        async with (
            httpx.AsyncClient(
                timeout=self.timeout_s, follow_redirects=False, transport=self.transport
            ) as client,
            client.stream(method, pinned, headers=sent, extensions={"sni_hostname": host}) as response,
        ):
            body = bytearray()
            truncated = False
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > max_bytes:
                    del body[max_bytes:]
                    truncated = True
                    break
            return WebResponse(
                response.status_code,
                {k.lower(): v for k, v in response.headers.items()},
                bytes(body),
                truncated,
            )


class FakeWebClient:
    """Scripted pages by URL, no network (ADR 0005): `pages[url] = (status, headers, body)`."""

    def __init__(self) -> None:
        self.pages: dict[str, tuple[int, dict[str, str], bytes]] = {}
        #: (method, url, headers, address), in order
        self.requests: list[tuple[str, str, dict[str, str], str]] = []

    def add(
        self,
        url: str,
        body: str | bytes = "",
        *,
        status: int = 200,
        content_type: str = "text/html",
        **headers: str,
    ) -> None:
        data = body.encode() if isinstance(body, str) else body
        self.pages[url] = (
            status,
            {"content-type": content_type, **{k.replace("_", "-"): v for k, v in headers.items()}},
            data,
        )

    async def request(
        self, method: str, url: str, *, headers: dict[str, str], address: str, max_bytes: int
    ) -> WebResponse:
        self.requests.append((method, url, dict(headers), address))
        status, answer_headers, data = self.pages.get(
            url, (404, {"content-type": "text/plain"}, b"not found")
        )
        if method == "HEAD":
            data = b""
        return WebResponse(status, dict(answer_headers), data[:max_bytes], len(data) > max_bytes)


_CLIENT: dict[str, WebClient] = {}


def set_web_client(client: WebClient | None) -> None:
    """Replaces the client (the tests); `None` goes back to the default."""
    _CLIENT.clear()
    if client is not None:
        _CLIENT["current"] = client


def get_web_client(fakes: bool) -> WebClient:
    if "current" not in _CLIENT:
        _CLIENT["current"] = FakeWebClient() if fakes else HttpWebClient()
    return _CLIENT["current"]


_RESOLVER: dict[str, Resolver] = {}


def set_resolver(resolver: Resolver | None) -> None:
    _RESOLVER.clear()
    if resolver is not None:
        _RESOLVER["current"] = resolver


def get_resolver() -> Resolver:
    return _RESOLVER.get("current", system_resolver)


# ───────────────────────────── robots.txt ─────────────────────────────


@dataclass
class Robots:
    parser: RobotFileParser | None
    #: why every fetch of the origin is refused, when it is
    blocked: str | None
    expires: float


_ROBOTS: dict[str, Robots] = {}
Clock = Callable[[], float]
_clock: dict[str, Clock] = {}


def set_clock(clock: Clock | None) -> None:
    """Replaces the monotonic clock of the robots cache (the tests)."""
    _clock.clear()
    if clock is not None:
        _clock["current"] = clock


def _now() -> float:
    return _clock.get("current", time.monotonic)()


def clear_robots() -> None:
    _ROBOTS.clear()


# ───────────────────────────── the refusal and the answer ─────────────────────────────


class Refused(Exception):  # noqa: N818 - a motivated refusal, rendered as a tool result
    def __init__(self, code: int, kind: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code, self.kind, self.message, self.extra = code, kind, message, extra

    def body(self) -> dict[str, Any]:
        return {"error": self.message, "type": self.kind, **self.extra}


class _Text(HTMLParser):
    """HTML as text: no script, no style, a line break where a block ends."""

    BLOCKS: ClassVar[set[str]] = {
        "p",
        "div",
        "br",
        "li",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "section",
        "article",
        "pre",
        "table",
    }
    SKIP: ClassVar[set[str]] = {"script", "style", "noscript", "template", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    """(text, title) of an HTML page."""
    parser = _Text()
    parser.feed(html)
    parser.close()
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    text = "\n".join(line for line in lines if line)
    return text, " ".join(parser.title.split())


def _media_type(headers: dict[str, str]) -> str:
    return headers.get("content-type", "").split(";")[0].strip().lower()


def _charset(headers: dict[str, str]) -> str:
    for part in headers.get("content-type", "").split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset" and value:
            return value.strip("\"'")
    return "utf-8"


def _decode(body: bytes, headers: dict[str, str]) -> str:
    try:
        return body.decode(_charset(headers), errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


# ───────────────────────────── the tool ─────────────────────────────


@dataclass
class Context:
    session: AsyncSession
    run: Run
    project: Project
    client: WebClient
    resolver: Resolver
    agent: str
    allow: list[str]
    deny: list[str]
    per_host: int | None
    per_run: int | None
    #: the hosts already refused in this call, for the journal
    seen: set[str] = field(default_factory=set)


def _parse(raw: str) -> tuple[str, str, str]:
    """(scheme, host, url) of a requested address, or a refusal."""
    parts = urlsplit(raw.strip())
    if parts.scheme not in {"https", "http"}:
        raise Refused(400, "bad_url", f"`{raw}`: only https:// (or http://) addresses can be read")
    if parts.username or parts.password:
        raise Refused(400, "bad_url", "the address carries credentials: write it without them")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise Refused(400, "bad_url", f"`{raw}` names no host")
    default = 443 if parts.scheme == "https" else 80
    try:
        port = parts.port or default
    except ValueError as bad:
        raise Refused(400, "bad_url", f"`{raw}` has an invalid port") from bad
    if port != default:
        raise Refused(
            403, "network_policy", f"port {port} is not reachable: only the default port of {parts.scheme} is"
        )
    return parts.scheme, host, parts._replace(fragment="").geturl()


async def _check_host(ctx: Context, host: str, *, hop: str = "") -> str:
    """The host against the run's network, then the address it resolves to; returns the address to
    connect to. A refusal is journaled once per host and call."""
    ok, reason = allowed(host, ctx.allow, ctx.deny)
    if not ok:
        await _refuse_journal(ctx, host, "denied", reason)
        what = f"{hop}`{host}` is refused for this run ({reason})"
        raise Refused(
            403,
            "network_policy",
            f"{what}: the agent's network, the project's policy or the organisation's "
            "network access decides — "
            "ask an administrator to allow it",
        )
    try:
        addresses = await ctx.resolver(host)
    except OSError as bad:
        raise Refused(502, "unreachable", f"`{host}` does not resolve: {bad}") from bad
    if not addresses:
        raise Refused(502, "unreachable", f"`{host}` does not resolve")
    for address in addresses:
        if is_private_address(address):
            await _refuse_journal(ctx, host, "denied", "private_address")
            raise Refused(
                403, "network_policy", f"{hop}`{host}` resolves to a private address: it is not read"
            )
    return addresses[0]


async def _refuse_journal(ctx: Context, host: str, decision: str, reason: str) -> None:
    key = f"{host}|{decision}|{reason}"
    if key in ctx.seen:
        return
    ctx.seen.add(key)
    await journal(
        ctx.session,
        ctx.project,
        host,
        decision,
        reason,
        "broker",
        run_id=ctx.run.id,
        agent_slug=ctx.run.agent_slug,
    )


async def _robots(ctx: Context, scheme: str, host: str, address: str) -> Robots:
    origin = f"{scheme}://{host}"
    cached = _ROBOTS.get(origin)
    if cached is not None and cached.expires > _now():
        return cached
    headers = {"User-Agent": ctx.agent, "Accept": "text/plain"}
    entry: Robots
    try:
        response = await ctx.client.request(
            "GET", f"{origin}/robots.txt", headers=headers, address=address, max_bytes=512 * 1024
        )
    except (httpx.HTTPError, OSError) as bad:
        entry = Robots(
            None,
            f"robots.txt of {host} could not be read ({type(bad).__name__}): nothing is read until it can",
            _now() + 60,
        )
    else:
        parser = RobotFileParser()
        if response.status == 200:
            parser.parse(_decode(response.body, response.headers).splitlines())
            entry = Robots(parser, None, _now() + ROBOTS_TTL_S)
        elif response.status in {401, 403}:
            entry = Robots(
                None,
                f"robots.txt of {host} is protected ({response.status}): treated as closed",
                _now() + ROBOTS_TTL_S,
            )
        elif response.status >= 500:
            entry = Robots(
                None,
                f"robots.txt of {host} answers {response.status}: nothing is read until it answers",
                _now() + 60,
            )
        else:
            parser.parse([])
            entry = Robots(parser, None, _now() + ROBOTS_TTL_S)
    _ROBOTS[origin] = entry
    return entry


async def _politeness(ctx: Context, host: str, crawl_delay: float | None) -> None:
    """429 when the host, or the run, was called too often, or inside the site's `Crawl-delay`."""
    org_id = ctx.project.org_id
    now = utcnow()
    if ctx.per_host:
        used = await calls_to_host(ctx.session, org_id, host, now=now)
        if used >= ctx.per_host:
            await _refuse_journal(ctx, host, "rate_limited", f"per_host_per_minute:{ctx.per_host}")
            raise Refused(
                429,
                "rate_limited",
                f"`{host}` was already called {used} times in the last minute "
                f"(limit {ctx.per_host}): wait, then retry",
                retry_after_s=60,
            )
    if ctx.per_run:
        mine = (
            await ctx.session.execute(
                select(func.count()).where(
                    CostLedger.run_id == ctx.run.id,
                    CostLedger.kind == "tool",
                    CostLedger.model == TOOL_NAME,
                    CostLedger.ts >= now - timedelta(seconds=60),
                )
            )
        ).scalar_one()
        if mine >= ctx.per_run:
            await _refuse_journal(ctx, host, "rate_limited", f"per_run_per_minute:{ctx.per_run}")
            raise Refused(
                429,
                "rate_limited",
                f"this run already fetched {mine} pages in the last minute "
                f"(limit {ctx.per_run}): wait, then retry",
                retry_after_s=60,
            )
    if crawl_delay:
        last = (
            await ctx.session.execute(
                select(func.max(CostLedger.ts))
                .join(Project, Project.id == CostLedger.project_id)
                .where(
                    Project.org_id == org_id, CostLedger.kind == "tool", CostLedger.provider == f"web:{host}"
                )
            )
        ).scalar_one()
        if last is not None:
            from choregos_core import aware

            waited = (now - (aware(last) or now)).total_seconds()
            if waited < crawl_delay:
                wait = max(1, int(crawl_delay - waited + 0.999))
                await _refuse_journal(ctx, host, "rate_limited", f"crawl_delay:{crawl_delay:g}")
                raise Refused(
                    429,
                    "rate_limited",
                    f"`{host}` asks for {crawl_delay:g} s between requests (Crawl-delay): retry in {wait} s",
                    retry_after_s=wait,
                )


async def fetch(
    session: AsyncSession, run: Run, project: Project, arguments: dict[str, Any], *, fakes: bool
) -> tuple[int, dict[str, Any], str]:
    """Reads one page. Returns (status code, result, provider) — the provider (`web:<host>`) is what the
    ledger and the per-host rate count, the host of the page asked for."""
    network = await run_network(session, run)
    ctx = Context(
        session=session,
        run=run,
        project=project,
        client=get_web_client(fakes),
        resolver=get_resolver(),
        agent=await user_agent(session, project.org_id),
        allow=[*network.allow, *network.implied],
        deny=network.deny,
        per_host=_least(network.per_host_per_minute, network.org_per_host_per_minute),
        per_run=network.per_run_per_minute,
    )
    method = str(arguments.get("method") or "GET").upper()
    cap = min(int(arguments.get("max_bytes") or MAX_BYTES), MAX_BYTES)
    try:
        scheme, host, url = _parse(str(arguments["url"]))
    except Refused as refused:
        return refused.code, refused.body(), f"web:{urlsplit(str(arguments.get('url', ''))).hostname or '-'}"
    first_host = host
    try:
        for hop in range(MAX_REDIRECTS + 1):
            address = await _check_host(ctx, host, hop="the redirect to " if hop else "")
            robots = await _robots(ctx, scheme, host, address)
            if robots.blocked:
                raise Refused(403, "robots_disallowed", robots.blocked)
            if robots.parser is not None and not robots.parser.can_fetch(ROBOTS_TOKEN, url):
                raise Refused(
                    403,
                    "robots_disallowed",
                    f"robots.txt of `{host}` disallows `{urlsplit(url).path or '/'}` "
                    f"for {ROBOTS_TOKEN}: it is not read",
                )
            delay = robots.parser.crawl_delay(ROBOTS_TOKEN) if robots.parser is not None else None
            await _politeness(ctx, host, float(delay) if delay else None)
            headers = {
                "User-Agent": ctx.agent,
                "Accept": "text/html, text/plain, text/markdown, application/json;q=0.9, */*;q=0.1",
                "Accept-Encoding": "identity",
            }
            try:
                response = await ctx.client.request(
                    method, url, headers=headers, address=address, max_bytes=cap
                )
            except (httpx.HTTPError, OSError) as bad:
                await _refuse_journal(ctx, host, "unreachable", type(bad).__name__)
                raise Refused(
                    502, "unreachable", f"`{host}` could not be reached: {type(bad).__name__}"
                ) from bad
            if response.status in {301, 302, 303, 307, 308} and response.headers.get("location"):
                if hop == MAX_REDIRECTS:
                    raise Refused(
                        502, "too_many_redirects", f"more than {MAX_REDIRECTS} redirects from `{first_host}`"
                    )
                scheme, host, url = _parse(urljoin(url, response.headers["location"]))
                continue
            code, result = await _answer(ctx, method, url, host, response, hop)
            return code, result, f"web:{first_host}"
    except Refused as refused:
        return refused.code, refused.body(), f"web:{first_host}"
    raise AssertionError("unreachable")  # pragma: no cover - the loop returns or raises


def _least(*values: int | None) -> int | None:
    present = [v for v in values if v]
    return min(present) if present else None


async def _answer(
    ctx: Context, method: str, url: str, host: str, response: WebResponse, hops: int
) -> tuple[int, dict[str, Any]]:
    """What the page said, as a tool result: its text, and what a person would check (status, final
    address, type, size, whether it was cut). A site that asks us to slow down is a 429 for the agent."""
    if response.status == 429:
        wait = response.headers.get("retry-after", "")
        await _refuse_journal(ctx, host, "rate_limited", "upstream_429")
        raise Refused(
            429,
            "rate_limited",
            f"`{host}` answered 429: it asks to be called less often",
            retry_after_s=int(wait) if wait.isdigit() else 60,
        )
    media = _media_type(response.headers)
    title = ""
    if method == "HEAD":
        shown = [f"HTTP {response.status}"] + [
            f"{name}: {response.headers[name]}"
            for name in ("content-type", "content-length", "last-modified", "etag")
            if name in response.headers
        ]
        text = "\n".join(shown)
    else:
        if response.body and media not in TEXT_TYPES and not media.endswith("+json"):
            raise Refused(
                415,
                "unsupported_content_type",
                f"`{media or 'unknown type'}` is not returned: "
                "only text, HTML (as text), JSON and markdown are",
            )
        raw = _decode(response.body, response.headers)
        text, title = html_to_text(raw) if media in {"text/html", "application/xhtml+xml"} else (raw, "")
    await journal(
        ctx.session, ctx.project, host, "allowed", TOOL_NAME, "broker",
        run_id=ctx.run.id, agent_slug=ctx.run.agent_slug, bytes_down=len(response.body),
    )  # fmt: skip
    structured: dict[str, Any] = {
        "url": url,
        "status": response.status,
        "content_type": media,
        "bytes": len(response.body),
        "truncated": response.truncated,
        "redirects": hops,
        **({"title": title} if title else {}),
    }
    return 200, {"content": [{"type": "text", "text": text}], "structuredContent": structured}


__all__ = [
    "INPUT_SCHEMA",
    "TOOL_NAME",
    "FakeWebClient",
    "HttpWebClient",
    "announcement",
    "fetch",
    "offered",
    "set_clock",
    "set_resolver",
    "set_web_client",
]
