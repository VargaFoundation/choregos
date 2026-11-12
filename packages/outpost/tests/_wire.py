# SPDX-License-Identifier: Apache-2.0
"""Real sockets for the tests that need them: a door served over HTTP or TLS on the loopback, an
outbound proxy, a private certificate authority. Every key here is made when the test runs."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import socket
import ssl
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

Handler = Callable[[httpx.Request], Awaitable[httpx.Response] | httpx.Response]
AUTHORITY = "Example Organisation Inspection Authority"


def _name(common: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common)])


def private_authority(directory: Path) -> tuple[Path, ssl.SSLContext]:
    """A certificate authority nobody trusts by default, and a server context whose certificate it
    signed for `localhost`: (the authority's certificate file, the server's TLS context)."""
    directory.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = (
        x509.CertificateBuilder()
        .subject_name(_name(AUTHORITY))
        .issuer_name(_name(AUTHORITY))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    names = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    leaf = (
        x509.CertificateBuilder()
        .subject_name(_name("localhost"))
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    pem = serialization.Encoding.PEM
    (directory / "ca.pem").write_bytes(ca.public_bytes(pem))
    (directory / "server.pem").write_bytes(leaf.public_bytes(pem))
    (directory / "server.key").write_bytes(
        key.private_bytes(pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(directory / "server.pem", directory / "server.key")
    return directory / "ca.pem", context


@contextlib.asynccontextmanager
async def served(
    handler: Handler, *, tls: ssl.SSLContext | None = None, origin: Callable[[str], None] | None = None
) -> AsyncIterator[str]:
    """Serves `handler` over HTTP/1.1 on the loopback, one request per connection; yields its origin."""
    base = [""]

    async def connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
            request_line, *lines = head.split("\r\n")
            method, target, _ = request_line.split(" ", 2)
            headers = [tuple(line.split(": ", 1)) for line in lines if ": " in line]
            length = int({name.lower(): value for name, value in headers}.get("content-length", "0"))
            body = await reader.readexactly(length)
            answer = handler(httpx.Request(method, base[0] + target, headers=headers, content=body))
            response = await answer if asyncio.iscoroutine(answer) else answer
            assert isinstance(response, httpx.Response)
            skipped = {"content-length", "connection", "transfer-encoding", "content-encoding"}
            out = [f"HTTP/1.1 {response.status_code} Answered"]
            out += [
                f"{name}: {value}" for name, value in response.headers.items() if name.lower() not in skipped
            ]
            out += [f"Content-Length: {len(response.content)}", "Connection: close", "", ""]
            writer.write("\r\n".join(out).encode("latin-1") + response.content)
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError, asyncio.CancelledError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(connection, "127.0.0.1", 0, ssl=tls)
    port = server.sockets[0].getsockname()[1]
    base[0] = f"{'https' if tls else 'http'}://{'localhost' if tls else '127.0.0.1'}:{port}"
    if origin is not None:
        origin(base[0])
    try:
        yield base[0]
    finally:
        server.close()


@contextlib.asynccontextmanager
async def proxy(answer: str = "200 Connection established") -> AsyncIterator[tuple[str, list[str]]]:
    """An outbound proxy on the loopback. It opens the tunnel it is asked for, or, with another
    `answer`, refuses it. Yields its URL and the targets it was asked to reach."""
    asked: list[str] = []

    async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(ConnectionError, asyncio.CancelledError):
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()
        writer.close()

    async def connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
            method, target, _ = head.split("\r\n", 1)[0].split(" ", 2)
            asked.append(f"{method} {target}")
            writer.write(f"HTTP/1.1 {answer}\r\n\r\n".encode())
            await writer.drain()
            if not answer.startswith("200"):
                writer.close()
                return
            host, port = target.rsplit(":", 1)
            upstream_reader, upstream_writer = await asyncio.open_connection(host, int(port))
            await asyncio.gather(pipe(reader, upstream_writer), pipe(upstream_reader, writer))
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            writer.close()

    server = await asyncio.start_server(connection, "127.0.0.1", 0)
    try:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}", asked
    finally:
        server.close()


def closed_port() -> int:
    """A port of the loopback nothing listens on."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


class Background:
    """An event loop in a thread: what serves a door while a command line runs in the test's thread."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self._thread.start()
        self._stacks: list[contextlib.AsyncExitStack] = []

    def run[Result](self, awaited: Coroutine[Any, Any, Result]) -> Result:
        return asyncio.run_coroutine_threadsafe(awaited, self.loop).result(timeout=30)

    def serve(self, handler: Handler, **more: Any) -> str:
        async def enter() -> str:
            stack = contextlib.AsyncExitStack()
            self._stacks.append(stack)
            return await stack.enter_async_context(served(handler, **more))

        return self.run(enter())

    def close(self) -> None:
        async def leave() -> None:
            for stack in self._stacks:
                await stack.aclose()

        self.run(leave())
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5)
