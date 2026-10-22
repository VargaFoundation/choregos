# SPDX-License-Identifier: Apache-2.0
"""The internet a hosted container reaches on `127.0.0.1:4003` (ADR 0080): an HTTP CONNECT proxy.

The container's `HTTPS_PROXY` points here. A tunnel to a host of `deny_domains` is refused, and so is
any host outside `allow_domains` when the step's list is complete (`egress: listed`). An accepted
tunnel goes through the pod's own egress proxy (`choregos-egress`) when the runner has one, with the
run's credentials, which the container never sees; the cluster's network policy stays the wall
behind this one. Plain HTTP through the proxy is refused: a hosted container speaks TLS or nothing.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
from collections.abc import Mapping
from urllib.parse import unquote, urlsplit

from choregos_contracts import StageInput
from choregos_core.egress import host_matches, normalize_host

from . import Record


class ConnectProxy:
    def __init__(self, stage_input: StageInput, record: Record, *, upstream: str = "") -> None:
        """`upstream`: the pod's egress proxy (`http://user:password@host:port`), or empty."""
        self.stage_input = stage_input
        self.record = record
        self.upstream = upstream
        self.tunnels = 0
        self.refused = 0

    def refusal(self, host: str) -> str | None:
        """Why a tunnel to `host` is refused, or `None`."""
        permissions = self.stage_input.permissions
        if any(host_matches(normalize_host(p, allow_wildcard=True), host) for p in permissions.deny_domains):
            return "denied"
        if permissions.egress == "listed" or permissions.allow_domains:
            allowed = [normalize_host(p, allow_wildcard=True) for p in permissions.allow_domains]
            if not any(host_matches(p, host) for p in allowed):
                return "not_allowed"
        return None

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            writer.close()
            return
        request_line = head.split(b"\r\n", 1)[0].decode("latin-1")
        method, _, rest = request_line.partition(" ")
        target = rest.rpartition(" ")[0]
        if method.upper() != "CONNECT":
            await self.record("hosted.egress_refused", {"target": target[:200], "reason": "not_connect"})
            await _reply(writer, 405, "only CONNECT (TLS) leaves a hosted container")
            return
        host, _, port_text = target.rpartition(":")
        host = normalize_host(host.strip("[]"))
        port = int(port_text) if port_text.isdigit() else 443
        if (why := self.refusal(host)) is not None:
            self.refused += 1
            await self.record("hosted.egress_refused", {"host": host, "port": port, "reason": why})
            await _reply(writer, 403, f"the step's network refuses {host}")
            return
        try:
            remote_reader, remote_writer = await self._open(host, port)
        except OSError as exc:
            await self.record("hosted.egress_failed", {"host": host, "port": port, "error": str(exc)[:200]})
            await _reply(writer, 502, "the tunnel could not be opened")
            return
        self.tunnels += 1
        await self.record("hosted.egress", {"host": host, "port": port})
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        await asyncio.gather(_pipe(reader, remote_writer), _pipe(remote_reader, writer))

    async def _open(self, host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if not self.upstream:
            return await asyncio.open_connection(host, port)
        parts = urlsplit(self.upstream)
        reader, writer = await asyncio.open_connection(parts.hostname, parts.port or 80)
        headers = [f"CONNECT {host}:{port} HTTP/1.1", f"Host: {host}:{port}"]
        if parts.username is not None:
            raw = f"{unquote(parts.username)}:{unquote(parts.password or '')}".encode()
            headers.append(f"Proxy-Authorization: Basic {base64.b64encode(raw).decode()}")
        writer.write(("\r\n".join(headers) + "\r\n\r\n").encode())
        await writer.drain()
        answer = await reader.readuntil(b"\r\n\r\n")
        status = answer.split(b" ", 2)[1:2]
        if status != [b"200"]:
            writer.close()
            raise OSError(f"the egress proxy answered {answer.split(b'\r\n', 1)[0].decode('latin-1')[:100]}")
        return reader, writer


def upstream_proxy(env: Mapping[str, str]) -> str:
    """The pod's egress proxy, as the runner's environment names it (after `authenticate_proxy_env`)."""
    return env.get("HTTPS_PROXY") or env.get("https_proxy") or ""


async def _reply(writer: asyncio.StreamWriter, status: int, text: str) -> None:
    reason = {403: "Forbidden", 405: "Method Not Allowed", 502: "Bad Gateway"}.get(status, "Error")
    body = text.encode()
    writer.write(
        f"HTTP/1.1 {status} {reason}\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n".encode()
        + body
    )
    with contextlib.suppress(ConnectionError):
        await writer.drain()
    writer.close()


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65_536):
            writer.write(data)
            await writer.drain()
    except ConnectionError:
        pass
    finally:
        with contextlib.suppress(Exception):
            writer.close()
