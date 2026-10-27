# SPDX-License-Identifier: Apache-2.0
"""The outbound-proxy relay (S40-26d, ADR 0091): a small forward proxy in the cluster.

SearXNG (`outgoing.proxies`) and Firecrawl (`PROXY_SERVER`) cannot learn an organisation's setting, so
they point here. The client's proxy username picks the kind of traffic (`search_engines`, `web_search`),
its password is the relay's client token. For each connection the relay asks which proxy that kind goes
through (`ProxySource`), then:

- a proxy applies: `CONNECT <name>:<port>` to it (an http target is forwarded in absolute form), with
  the organisation's credentials; anything the proxy refuses is a 502 naming the proxy, never a
  direct connection;
- none applies: a direct connection, after resolving the name and refusing any private, loopback,
  link-local or cluster address, pinned to the address that was checked;
- the relay cannot tell (the API cannot be asked): a 502, nothing is sent.

One request per connection for an http target (`Connection: close`): a second request on the same
connection could name another host.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import hmac
import socket
import ssl
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from choregos_adapters.documents.web import is_private_address
from choregos_core.outbound_proxy import ProxyConfig
from choregos_core.redaction import redact

from ..logging import get_logger
from .source import ProxySource, ProxySourceError

#: The kinds a relay client may name (ADR 0091 §2).
RELAY_KINDS: tuple[str, ...] = ("search_engines", "web_search")
MAX_HEAD_BYTES = 16 * 1024
MAX_BODY_BYTES = 8 * 1024 * 1024
HEAD_TIMEOUT_S = 15.0
CONNECT_TIMEOUT_S = 10.0
IDLE_TIMEOUT_S = 300.0
_HOP_HEADERS = {"proxy-authorization", "proxy-connection", "connection", "keep-alive"}

Streams = tuple[asyncio.StreamReader, asyncio.StreamWriter]
Resolver = Callable[[str, int], Awaitable[list[str]]]
Connector = Callable[[str, int, ssl.SSLContext | None, str | None], Awaitable[Streams]]

log = get_logger(__name__)


class RelayRefusalError(Exception):
    def __init__(self, status: int, reason: str, detail: str, *, challenge: bool = False) -> None:
        super().__init__(detail)
        self.status = status
        self.reason = reason
        self.detail = detail
        self.challenge = challenge


@dataclass(frozen=True, slots=True)
class Head:
    method: str
    target: str
    version: str
    headers: list[tuple[str, str]]

    def header(self, name: str) -> str | None:
        return next((v for k, v in self.headers if k.lower() == name), None)


async def system_resolver(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


async def open_tcp(host: str, port: int, tls: ssl.SSLContext | None, server_hostname: str | None) -> Streams:
    return await asyncio.open_connection(host, port, ssl=tls, server_hostname=server_hostname)


async def _read_head(reader: asyncio.StreamReader) -> bytes:
    try:
        return await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT_S)
    except asyncio.LimitOverrunError:
        raise RelayRefusalError(
            431, "Request Header Fields Too Large", "the request head is too large"
        ) from None
    except (asyncio.IncompleteReadError, TimeoutError):
        raise RelayRefusalError(400, "Bad Request", "the request head is incomplete") from None


def _parse_head(raw: bytes) -> Head:
    try:
        text = raw.decode("latin-1")
        line, *rest = text.split("\r\n")
        method, target, version = line.split(" ")
    except ValueError:
        raise RelayRefusalError(400, "Bad Request", "the request line is malformed") from None
    headers: list[tuple[str, str]] = []
    for item in rest:
        if not item:
            continue
        name, sep, value = item.partition(":")
        if not sep:
            raise RelayRefusalError(400, "Bad Request", "a header is malformed")
        headers.append((name.strip(), value.strip()))
    return Head(method.upper(), target, version, headers)


async def _read_body(reader: asyncio.StreamReader, head: Head) -> bytes:
    """An http request's body, read whole before anything is sent upstream (bounded): through a proxy
    the relay waits for its answer head, which it must not do while the body is still in the client."""
    if head.method == "CONNECT":
        return b""
    if head.header("transfer-encoding"):
        raise RelayRefusalError(
            411, "Length Required", "the relay forwards a body with a Content-Length only"
        )
    try:
        length = int(head.header("content-length") or 0)
    except ValueError:
        raise RelayRefusalError(400, "Bad Request", "the Content-Length is malformed") from None
    if not 0 <= length <= MAX_BODY_BYTES:
        raise RelayRefusalError(413, "Content Too Large", "the request body is too large")
    try:
        return await asyncio.wait_for(reader.readexactly(length), HEAD_TIMEOUT_S) if length else b""
    except (asyncio.IncompleteReadError, TimeoutError):
        raise RelayRefusalError(400, "Bad Request", "the request body is incomplete") from None


def _response(status: int, reason: str, detail: str, *, challenge: bool = False) -> bytes:
    body = (detail + "\n").encode()
    lines = [
        f"HTTP/1.1 {status} {reason}",
        "Content-Type: text/plain; charset=utf-8",
        f"Content-Length: {len(body)}",
        "Connection: close",
    ]
    if challenge:
        lines.append('Proxy-Authenticate: Basic realm="choregos-relay"')
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


def _host_port(authority: str, default_port: int | None) -> tuple[str, int]:
    try:
        parts = urlsplit(f"//{authority}")
        host, port = parts.hostname, parts.port or default_port
    except ValueError:
        raise RelayRefusalError(400, "Bad Request", "the target is malformed") from None
    if not host or port is None or not 1 <= port <= 65535:
        raise RelayRefusalError(400, "Bad Request", "the target names no host and port")
    return host, port


class Relay:
    def __init__(
        self,
        source: ProxySource,
        client_token: str,
        *,
        resolver: Resolver = system_resolver,
        connect: Connector = open_tcp,
    ) -> None:
        if not client_token:
            raise ValueError("the relay needs a client token")
        self._source = source
        self._client_token = client_token.encode()
        self._resolve = resolver
        self._connect = connect

    # ── the client side ──

    def _kind(self, head: Head) -> str:
        """The kind the client names, once its token is checked; a 407 otherwise."""
        value = head.header("proxy-authorization") or ""
        scheme, _, encoded = value.partition(" ")
        try:
            user, _, secret = base64.b64decode(encoded.strip(), validate=True).decode().partition(":")
        except (binascii.Error, UnicodeDecodeError):
            user, secret = "", ""
        if scheme.lower() != "basic" or not hmac.compare_digest(secret.encode(), self._client_token):
            raise RelayRefusalError(
                407, "Proxy Authentication Required", "the relay's client token is required", challenge=True
            )
        if user not in RELAY_KINDS:
            raise RelayRefusalError(
                407,
                "Proxy Authentication Required",
                f"the username names the kind of traffic: one of {', '.join(RELAY_KINDS)}",
                challenge=True,
            )
        return user

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        proxy: ProxyConfig | None = None
        try:
            head = _parse_head(await _read_head(reader))
            kind = self._kind(head)
            if head.method == "CONNECT":
                host, port = _host_port(head.target, None)
            else:
                target = urlsplit(head.target)
                if target.scheme != "http":
                    raise RelayRefusalError(400, "Bad Request", "an https target goes through CONNECT")
                host, port = _host_port(target.netloc, 80)
            body = await _read_body(reader, head)
            try:
                proxy = await self._source.get(kind)
            except ProxySourceError as unknown:
                raise RelayRefusalError(
                    502,
                    "Bad Gateway",
                    f"the relay cannot tell whether {kind} goes through the organisation's proxy "
                    f"({unknown}): nothing is sent",
                ) from None
            if proxy is not None:
                upstream = await self._via_proxy(proxy, head, host, port, body)
            else:
                upstream = await self._direct(head, host, port, body)
            log.info("relay_open", kind=kind, host=host, port=port, proxied=proxy is not None)
        except RelayRefusalError as refused:
            detail = self._scrub(refused.detail, proxy)
            log.info("relay_refused", status=refused.status, detail=detail)
            await self._close_with(
                writer, _response(refused.status, refused.reason, detail, challenge=refused.challenge)
            )
            return
        except (ConnectionError, OSError):
            writer.close()
            return
        upstream_reader, upstream_writer, first = upstream
        try:
            writer.write(
                b"HTTP/1.1 200 Connection established\r\n\r\n" if head.method == "CONNECT" else first
            )
            await writer.drain()
        except (ConnectionError, OSError):
            upstream_writer.close()
            writer.close()
            return
        await self._pipe(reader, writer, upstream_reader, upstream_writer)

    # ── the upstream side ──

    async def _open(
        self, host: str, port: int, tls: ssl.SSLContext | None = None, name: str | None = None
    ) -> Streams:
        return await asyncio.wait_for(self._connect(host, port, tls, name), CONNECT_TIMEOUT_S)

    async def _via_proxy(
        self, proxy: ProxyConfig, head: Head, host: str, port: int, body: bytes
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, bytes]:
        proxy_host, proxy_port = _host_port(proxy.endpoint, None)
        tls = ssl.create_default_context() if proxy.scheme == "https" else None
        try:
            reader, writer = await self._open(proxy_host, proxy_port, tls, proxy_host if tls else None)
        except (OSError, TimeoutError, ssl.SSLError) as bad:
            raise RelayRefusalError(
                502,
                "Bad Gateway",
                f"the outbound proxy {proxy.label} could not be reached ({type(bad).__name__})",
            ) from None
        credentials = base64.b64encode(f"{proxy.username}:{proxy.password}".encode()).decode()
        authority = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        if head.method == "CONNECT":
            request = f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n"
        else:
            request = f"{head.method} {head.target} HTTP/1.1\r\n" + self._forwarded_headers(head)
        request += f"Proxy-Authorization: Basic {credentials}\r\n\r\n"
        writer.write(request.encode("latin-1") + body)
        await writer.drain()
        try:
            answer = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT_S)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError, OSError):
            writer.close()
            raise RelayRefusalError(
                502, "Bad Gateway", f"the outbound proxy {proxy.label} did not answer for `{host}`"
            ) from None
        status = self._status(answer)
        if status == 407:
            writer.close()
            raise RelayRefusalError(
                502, "Bad Gateway", f"the outbound proxy {proxy.label} refused the credentials (407)"
            )
        if head.method == "CONNECT" and status != 200:
            writer.close()
            raise RelayRefusalError(
                502, "Bad Gateway", f"the outbound proxy {proxy.label} refused `{authority}` (HTTP {status})"
            )
        # To an http request, the proxy's answer is the target's: handed back as it came.
        return reader, writer, b"" if head.method == "CONNECT" else answer

    async def _direct(
        self, head: Head, host: str, port: int, body: bytes
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, bytes]:
        try:
            addresses = await self._resolve(host, port)
        except OSError:
            raise RelayRefusalError(502, "Bad Gateway", f"`{host}` does not resolve") from None
        if not addresses:
            raise RelayRefusalError(502, "Bad Gateway", f"`{host}` does not resolve")
        if any(is_private_address(address) for address in addresses):
            raise RelayRefusalError(
                403, "Forbidden", f"`{host}` resolves to a private address: the relay does not reach it"
            )
        try:
            reader, writer = await self._open(addresses[0], port)
        except (OSError, TimeoutError) as bad:
            raise RelayRefusalError(
                502, "Bad Gateway", f"`{host}` could not be reached ({type(bad).__name__})"
            ) from None
        if head.method != "CONNECT":
            target = urlsplit(head.target)
            path = target.path or "/"
            if target.query:
                path += f"?{target.query}"
            request = f"{head.method} {path} HTTP/1.1\r\n" + self._forwarded_headers(head) + "\r\n"
            writer.write(request.encode("latin-1") + body)
            await writer.drain()
        return reader, writer, b""

    @staticmethod
    def _forwarded_headers(head: Head) -> str:
        kept = "".join(f"{k}: {v}\r\n" for k, v in head.headers if k.lower() not in _HOP_HEADERS)
        return kept + "Connection: close\r\n"

    @staticmethod
    def _status(answer: bytes) -> int:
        try:
            return int(answer.split(b" ", 2)[1])
        except (IndexError, ValueError):
            return 0

    # ── plumbing ──

    async def _pipe(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        upstream_reader: asyncio.StreamReader,
        upstream_writer: asyncio.StreamWriter,
    ) -> None:
        async def copy(source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
            try:
                while chunk := await asyncio.wait_for(source.read(65536), IDLE_TIMEOUT_S):
                    sink.write(chunk)
                    await sink.drain()
            except (ConnectionError, OSError, TimeoutError):
                pass
            finally:
                if sink.can_write_eof():
                    with contextlib.suppress(ConnectionError, OSError):
                        sink.write_eof()

        try:
            await asyncio.gather(copy(client_reader, upstream_writer), copy(upstream_reader, client_writer))
        finally:
            for side in (upstream_writer, client_writer):
                side.close()

    @staticmethod
    def _scrub(text: str, proxy: ProxyConfig | None) -> str:
        for form in proxy.secrets() if proxy else ():
            text = text.replace(form, "***")
        return redact(text)

    @staticmethod
    async def _close_with(writer: asyncio.StreamWriter, data: bytes) -> None:
        try:
            writer.write(data)
            await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()


__all__ = ["RELAY_KINDS", "Relay", "RelayRefusalError", "open_tcp", "system_resolver"]
