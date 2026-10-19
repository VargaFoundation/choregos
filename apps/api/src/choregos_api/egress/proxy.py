# SPDX-License-Identifier: Apache-2.0
"""The per-run egress proxy (ADR 0070): an asyncio HTTP proxy that knows which run is calling.

What it sees, and what it can therefore enforce: the run's identity (Basic proxy credentials = run
id and run token), the destination host and port of a `CONNECT`, or the request line of a plain HTTP
request. It enforces the run's effective network (deny before allow, ports, IP literals, private
addresses), and connection rates. It does NOT see inside a TLS tunnel: method, path, headers and
User-Agent are invisible to it, and one tunnel can carry many HTTP/2 requests, so its rate is a
CONNECTION rate. It adds no `Via` and no `X-Forwarded-For`.

The destination is resolved ONCE, every address must be public, and the connection goes to that
address: a name that resolves to an address inside the cluster is refused, and a second resolution
cannot be made to disagree with the first.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import ipaddress
import socket
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import structlog
from choregos_core.network import allowed, normalise_host

from ..schemas.network import RunNetwork
from .config import EgressSettings
from .limits import RateLimiter, retry_after
from .report import Reporter
from .sources import RunNetworkSource, SourceUnavailableError, UnknownRunError

log = structlog.get_logger("choregos.egress")

Resolver = Callable[[str, int], Awaitable[list[str]]]
PublicPredicate = Callable[[str], bool]

REASONS = {
    "ip_literal": "an IP address is never allowed: use the host name",
    "port_not_allowed": "only the ports {ports} are open",
    "private_address": "the name resolves to an address inside the network, which is never reachable",
    "not_allowed": (
        "allow it in the agent's Network section, the project's policy (sandbox.network.allow_domains) "
        "or the organisation's policy ceiling"
    ),
}
PHRASES = {
    200: "Connection Established",
    400: "Bad Request",
    403: "Forbidden",
    407: "Proxy Authentication Required",
    429: "Too Many Requests",
    431: "Request Header Fields Too Large",
    502: "Bad Gateway",
    503: "Service Unavailable",
}


async def system_resolver(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]).split("%")[0] for info in infos))


def is_public(address: str) -> bool:
    """Whether an address is a public one: private, loopback, link-local, reserved, multicast,
    unspecified and the IPv4-mapped forms of those are not."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast and not ip.is_unspecified


@dataclass
class Request:
    method: str
    target: str
    version: str
    headers: list[tuple[str, str]]

    def header(self, name: str) -> str | None:
        wanted = name.lower()
        return next((v for k, v in self.headers if k.lower() == wanted), None)


class BadRequestError(Exception):
    pass


def parse_head(raw: bytes) -> Request:
    try:
        text = raw.decode("latin-1")
    except UnicodeDecodeError as error:  # pragma: no cover - latin-1 decodes everything
        raise BadRequestError("not text") from error
    lines = text.split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/"):
        raise BadRequestError("malformed request line")
    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        if not line:
            continue
        name, sep, value = line.partition(":")
        if not sep or not name.strip():
            raise BadRequestError("malformed header")
        headers.append((name.strip(), value.strip()))
    return Request(parts[0].upper(), parts[1], parts[2], headers)


def credentials(request: Request) -> tuple[str, str] | None:
    value = request.header("Proxy-Authorization")
    if not value or not value.lower().startswith("basic "):
        return None
    try:
        decoded = base64.b64decode(value[6:].strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    run_id, sep, token = decoded.partition(":")
    return (run_id, token) if sep and run_id and token else None


def split_authority(authority: str, default_port: int | None) -> tuple[str, int]:
    host, sep, port = authority.rpartition(":")
    if authority.startswith("["):  # an IPv6 literal: refused later, but parsed here
        host, _, rest = authority[1:].partition("]")
        port = rest.lstrip(":")
        sep = ":" if port else ""
    elif not sep:
        host, port = authority, ""
    if not host:
        raise BadRequestError("no host")
    if port:
        if not port.isdigit():
            raise BadRequestError("bad port")
        return host, int(port)
    if default_port is None:
        raise BadRequestError("no port")
    return host, default_port


class EgressProxy:
    def __init__(
        self,
        settings: EgressSettings,
        source: RunNetworkSource,
        reporter: Reporter,
        *,
        resolver: Resolver = system_resolver,
        public: PublicPredicate = is_public,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.source = source
        self.reporter = reporter
        self.resolver = resolver
        self.public = public
        self.limiter = RateLimiter(clock)
        self.tunnels: Counter[str] = Counter()
        self._server: asyncio.AbstractServer | None = None
        self._connections: set[asyncio.Task[None]] = set()

    # ───────────────────────── serving ─────────────────────────

    async def start(self, host: str = "0.0.0.0", port: int | None = None) -> int:  # noqa: S104
        self._server = await asyncio.start_server(
            self.handle, host, self.settings.port if port is None else port, limit=16 * 1024
        )
        return int(self._server.sockets[0].getsockname()[1])

    async def close(self) -> None:
        """Stop listening and end every open tunnel (`wait_closed` would wait for them forever)."""
        if self._server is not None:
            self._server.close()
        tasks = list(self._connections)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._server is not None:
            await self._server.wait_closed()

    # ───────────────────────── one client ─────────────────────────

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._connections.add(task)
        try:
            await self._serve(reader, writer)
        except (TimeoutError, ConnectionError, asyncio.IncompleteReadError):
            pass
        except Exception:  # a bug here must cost one connection, never the proxy
            log.exception("egress proxy error")
        finally:
            if task is not None:
                self._connections.discard(task)
            with contextlib.suppress(Exception):
                writer.close()

    async def _refuse(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        message: str,
        *,
        extra: dict[str, str] | None = None,
    ) -> None:
        body = (message.rstrip() + "\n").encode()
        head = [f"HTTP/1.1 {status} {PHRASES[status]}", "Content-Type: text/plain; charset=utf-8"]
        head += [f"Content-Length: {len(body)}", "Connection: close"]
        head += [f"{k}: {v}" for k, v in (extra or {}).items()]
        writer.write(("\r\n".join(head) + "\r\n\r\n").encode() + body)
        with contextlib.suppress(Exception):
            await writer.drain()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request = await self._read_request(reader, writer)
        if request is None:
            return
        creds = credentials(request)
        if creds is None:
            await self._refuse(
                writer,
                407,
                "choregos-egress: proxy credentials are required (the run id and run token); the runner "
                "adds them to HTTP_PROXY when CHOREGOS_EGRESS_AUTH=run",
                extra={"Proxy-Authenticate": 'Basic realm="choregos-egress"'},
            )
            return
        run_id, token = creds
        destination = await self._destination(request, writer)
        if destination is None:
            return
        host, port, absolute = destination
        network = await self._network_of(run_id, token, writer)
        if network is None:
            return
        admitted = await self._admit(run_id, token, host, port, network, writer)
        if admitted is None:
            return
        upstream, reason = admitted
        await self._tunnel(request, reader, writer, upstream, (run_id, token, host, port, absolute, reason))

    async def _read_request(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> Request | None:
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), self.settings.head_timeout_s)
            return parse_head(raw[:-4])
        except asyncio.LimitOverrunError:
            await self._refuse(writer, 431, "choregos-egress: the request head is too large")
        except BadRequestError as error:
            await self._refuse(writer, 400, f"choregos-egress: bad request ({error})")
        return None

    async def _destination(
        self, request: Request, writer: asyncio.StreamWriter
    ) -> tuple[str, int, str | None] | None:
        """(host, port, origin-form path or None for a CONNECT) of what the client asks for."""
        try:
            if request.method == "CONNECT":
                host, port = split_authority(request.target, None)
                return normalise_host(host), port, None
            if request.target.lower().startswith("http://"):
                parts = urlsplit(request.target)
                host, port = split_authority(parts.netloc.rpartition("@")[2], 80)
                return (
                    normalise_host(host),
                    port,
                    (parts.path or "/") + (f"?{parts.query}" if parts.query else ""),
                )
            await self._refuse(
                writer, 400, "choregos-egress: this is a proxy: send CONNECT or an absolute URL"
            )
        except BadRequestError as error:
            await self._refuse(writer, 400, f"choregos-egress: bad request ({error})")
        return None

    async def _network_of(self, run_id: str, token: str, writer: asyncio.StreamWriter) -> RunNetwork | None:
        try:
            return await self.source.get(run_id, token)
        except UnknownRunError:
            await self._refuse(
                writer,
                407,
                "choregos-egress: these proxy credentials are not a live run's",
                extra={"Proxy-Authenticate": 'Basic realm="choregos-egress"'},
            )
        except SourceUnavailableError as error:
            log.warning("run network unavailable", run_id=run_id, error=str(error)[:200])
            await self._refuse(
                writer, 503, "choregos-egress: the platform could not say what this run may reach; try again"
            )
        return None

    async def _deny(
        self,
        writer: asyncio.StreamWriter,
        call: tuple[str, str, str, int, RunNetwork],
        reason: str,
        status: int = 403,
        retry: int | None = None,
    ) -> None:
        run_id, token, host, port, network = call
        self.reporter.record(run_id, token, host, port, "rate_limited" if status == 429 else "denied", reason)
        log.info(
            "egress refused", run_id=run_id, project=network.project, host=host, port=port, reason=reason
        )
        hint = REASONS.get(reason.split(":", 1)[0], "")
        if reason.startswith("denied:"):
            hint = "the organisation, the project or the deployment denies it: remove it from the deny list"
        hint = hint.format(ports="/".join(str(p) for p in self.settings.ports))
        await self._refuse(
            writer,
            status,
            f"choregos-egress: {host}:{port} refused for this run ({reason}); {hint}".rstrip("; "),
            extra={"Retry-After": str(retry)} if retry is not None else None,
        )

    async def _admit(
        self,
        run_id: str,
        token: str,
        host: str,
        port: int,
        network: RunNetwork,
        writer: asyncio.StreamWriter,
    ) -> tuple[tuple[asyncio.StreamReader, asyncio.StreamWriter], str] | None:
        """Every check, in order; the upstream connection and why it was allowed when all pass,
        `None` after answering."""
        call = (run_id, token, host, port, network)
        if port not in self.settings.ports:
            await self._deny(writer, call, "port_not_allowed")
            return None
        ok, reason = allowed(
            host, [*network.allow, *network.implied], [*network.deny, *self.settings.deny_domains]
        )
        if not ok:
            await self._deny(writer, call, reason)
            return None
        wait = self.limiter.acquire(
            [
                ((run_id, host), network.per_host_per_minute),
                ((run_id, "*"), network.per_run_per_minute),
                ((network.org, host), network.org_per_host_per_minute),
            ]
        )
        if wait > 0:
            await self._deny(writer, call, "rate_limited", 429, retry_after(wait))
            return None
        if self.tunnels[run_id] >= self.settings.max_tunnels_per_run:
            await self._deny(writer, call, "too_many_tunnels", 429, 1)
            return None
        try:
            addresses = await self.resolver(host, port)
        except OSError:
            addresses = []
        if addresses and not all(self.public(a) for a in addresses):
            await self._deny(writer, call, "private_address")
            return None
        upstream = await self._connect(addresses, port)
        if upstream is None:
            self.reporter.record(run_id, token, host, port, "unreachable", "unreachable")
            await self._refuse(
                writer,
                502,
                f"choregos-egress: {host}:{port} is allowed for this run but could not be reached "
                "(unresolvable, refused or timed out): the block is below Choregos, check the network",
            )
            return None
        self.reporter.record(run_id, token, host, port, "allowed", reason)
        return upstream, reason

    async def _tunnel(
        self,
        request: Request,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        upstream: tuple[asyncio.StreamReader, asyncio.StreamWriter],
        who: tuple[str, str, str, int, str | None, str],
    ) -> None:
        run_id, token, host, port, absolute, reason = who
        up_reader, up_writer = upstream
        self.tunnels[run_id] += 1
        counters = [0, 0]
        try:
            if absolute is None:
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
            else:
                up_writer.write(self._forward_head(request, absolute, host, port))
                await up_writer.drain()
            await asyncio.gather(
                self._pipe(reader, up_writer, counters, 0), self._pipe(up_reader, writer, counters, 1)
            )
        finally:
            self.tunnels[run_id] -= 1
            if not self.tunnels[run_id]:
                del self.tunnels[run_id]
            self.reporter.record(
                run_id, token, host, port, "allowed", reason, count=0, up=counters[0], down=counters[1]
            )
            with contextlib.suppress(Exception):
                up_writer.close()

    async def _connect(
        self, addresses: list[str], port: int
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter] | None:
        for address in addresses:
            try:
                return await asyncio.wait_for(
                    asyncio.open_connection(address, port), self.settings.connect_timeout_s
                )
            except (TimeoutError, OSError):
                continue
        return None

    @staticmethod
    def _forward_head(request: Request, path: str, host: str, port: int) -> bytes:
        """The request as the origin should read it: origin-form, `Proxy-Authorization` and
        `Proxy-Connection` removed, `Connection: close` (one request per connection), and nothing
        added: no `Via`, no `X-Forwarded-For`."""
        lines = [f"{request.method} {path} HTTP/1.1"]
        has_host = False
        for name, value in request.headers:
            lowered = name.lower()
            if lowered in {"proxy-authorization", "proxy-connection", "connection", "keep-alive"}:
                continue
            has_host = has_host or lowered == "host"
            lines.append(f"{name}: {value}")
        if not has_host:
            lines.append(f"Host: {host}" if port == 80 else f"Host: {host}:{port}")
        lines.append("Connection: close")
        return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")

    async def _pipe(
        self, source: asyncio.StreamReader, sink: asyncio.StreamWriter, counters: list[int], slot: int
    ) -> None:
        try:
            while True:
                data = await asyncio.wait_for(source.read(65536), self.settings.idle_timeout_s)
                if not data:
                    break
                sink.write(data)
                await sink.drain()
                counters[slot] += len(data)
            with contextlib.suppress(Exception):
                if sink.can_write_eof():
                    sink.write_eof()
        except (TimeoutError, ConnectionError, OSError):
            with contextlib.suppress(Exception):
                sink.close()


async def serve_health(port: int, host: str = "0.0.0.0") -> asyncio.AbstractServer:  # noqa: S104
    """`/healthz` for the pod's probes: the process answers."""

    async def answer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\nConnection: close\r\n\r\nok\n")
            await writer.drain()
        with contextlib.suppress(Exception):
            writer.close()

    return await asyncio.start_server(answer, host, port)
