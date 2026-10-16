"""A proxy, an upstream and a client on 127.0.0.1, for the egress proxy's tests (real sockets)."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from choregos_api.egress.config import EgressSettings
from choregos_api.egress.proxy import EgressProxy
from choregos_api.egress.report import FakeDecisionSink, Reporter
from choregos_api.egress.sources import FakeRunNetworkSource

LOOPBACK = "127.0.0.1"


def basic(run_id: str, token: str) -> str:
    return "Basic " + base64.b64encode(f"{run_id}:{token}".encode()).decode()


@dataclass
class Upstream:
    """An echo server that also records the plain-HTTP requests it receives."""

    port: int
    requests: list[bytes] = field(default_factory=list)


@asynccontextmanager
async def upstream_server() -> AsyncIterator[Upstream]:
    seen: list[bytes] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            first = await reader.read(65536)
            if first.startswith((b"GET ", b"POST ")):
                seen.append(first)
                body = b"hello"
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nConnection: close\r\n\r\n" + body)
                await writer.drain()
            else:
                while first:
                    writer.write(first)
                    await writer.drain()
                    first = await reader.read(65536)
        finally:
            writer.close()

    server = await asyncio.start_server(handle, LOOPBACK, 0)
    try:
        yield Upstream(server.sockets[0].getsockname()[1], seen)
    finally:
        server.close()
        await server.wait_closed()


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@dataclass
class Rig:
    proxy: EgressProxy
    port: int
    source: FakeRunNetworkSource
    sink: FakeDecisionSink
    reporter: Reporter
    upstream: Upstream
    clock: Clock


@asynccontextmanager
async def rig(**settings: object) -> AsyncIterator[Rig]:
    """A proxy whose resolver maps `*.test` names to the loopback upstream, and whose private-address
    predicate lets the loopback through: the default predicate is tested on its own."""
    async with upstream_server() as upstream:
        source = FakeRunNetworkSource()
        sink = FakeDecisionSink()
        reporter = Reporter(sink)
        clock = Clock()
        config = EgressSettings(ports=[upstream.port, 22], **settings)  # type: ignore[arg-type]

        async def resolver(host: str, port: int) -> list[str]:
            if host.endswith(".test"):
                return [LOOPBACK]
            if host == "private.test2":
                return ["10.0.0.5"]
            raise OSError("no such host")

        proxy = EgressProxy(
            config, source, reporter, resolver=resolver, public=lambda a: a == LOOPBACK, clock=clock
        )
        port = await proxy.start("127.0.0.1", 0)
        try:
            yield Rig(proxy, port, source, sink, reporter, upstream, clock)
        finally:
            await proxy.close()


async def connect_request(
    port: int, target: str, *, auth: str | None = None, extra: list[str] | None = None
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, str, bytes]:
    """Send a CONNECT and return the open streams, the status line and the response body (read per
    Content-Length when there is one: a refusal)."""
    reader, writer = await asyncio.open_connection(LOOPBACK, port)
    lines = [f"CONNECT {target} HTTP/1.1", f"Host: {target}"]
    if auth:
        lines.append(f"Proxy-Authorization: {auth}")
    lines += extra or []
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode())
    await writer.drain()
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
    status = head.split(b"\r\n", 1)[0].decode()
    length = 0
    for line in head.decode().split("\r\n"):
        if line.lower().startswith("content-length:"):
            length = int(line.split(":", 1)[1])
    body = await asyncio.wait_for(reader.readexactly(length), 5) if length else b""
    return reader, writer, status, body + head


def headers_of(blob: bytes) -> dict[str, str]:
    text = blob.decode("latin-1")
    out = {}
    for line in text.split("\r\n")[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            out[key.strip().lower()] = value.strip()
    return out


def _policy(allow: list[str], **extra: Any) -> str:
    network = ", ".join([f"allow_domains: {allow!r}", *(f"{k}: {v}" for k, v in extra.items())]).replace(
        "'", '"'
    )
    return f"""apiVersion: choregos/v1
kind: Policy
metadata: {{ name: net, version: 1 }}
sandbox:
  network: {{ {network} }}
"""


async def _run(project: dict[str, Any], run_id: str, **columns: Any) -> dict[str, str]:
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key=f"BILLING-{run_id}", title="T", state="ready")
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id=run_id,
                work_item_id=item.id,
                project_id=project["id"],
                stage_role="implement",
                status="running",
                **columns,
            )
        )
    token = mint_run_token(
        run_id, project_slug="billing-api", work_item_key=f"BILLING-{run_id}", ttl_minutes=30
    )
    return {"Authorization": f"Bearer {token}"}
