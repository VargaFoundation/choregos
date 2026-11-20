"""The pod's egress proxy, for the agent-shape measurement (S65-04a): a CONNECT proxy that logs.

Standard library only: it runs in a container of the runner's image beside the pod, on the only network
that reaches out, and writes one JSON line per request to `--log`. It stands in for the per-run egress
proxy a cluster gives the runner (ADR 0070): the runner's `HTTPS_PROXY` names it, with the run's
identity as credentials, which this proxy records by user name only.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import time
from pathlib import Path
from urllib.parse import urlsplit


class Egress:
    def __init__(self, log: Path) -> None:
        self.log = log

    def write(self, entry: dict[str, object]) -> None:
        with self.log.open("a", encoding="utf-8") as out:
            out.write(json.dumps({"t": round(time.time(), 3), **entry}) + "\n")

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            writer.close()
            return
        lines = head.decode("latin-1").split("\r\n")
        method, _, rest = lines[0].partition(" ")
        target = rest.rpartition(" ")[0]
        user = ""
        for line in lines[1:]:
            name, _, value = line.partition(":")
            if name.strip().lower() == "proxy-authorization" and value.strip().lower().startswith("basic "):
                with contextlib.suppress(ValueError):
                    user = base64.b64decode(value.strip()[6:]).decode().partition(":")[0]
        if method.upper() != "CONNECT":
            await self.forward(method, target, user, lines, reader, writer)
            return
        host, _, port = target.rpartition(":")
        started = time.monotonic()
        try:
            remote_reader, remote_writer = await asyncio.wait_for(
                asyncio.open_connection(host.strip("[]"), int(port or 443)), 15
            )
        except (OSError, ValueError, TimeoutError) as exc:
            failure = {"method": "CONNECT", "host": host, "port": port, "user": user, "error": str(exc)[:200]}
            self.write(failure)
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\ncontent-length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        sent, received = await asyncio.gather(_pipe(reader, remote_writer), _pipe(remote_reader, writer))
        self.write(
            {
                "method": "CONNECT",
                "host": host,
                "port": port,
                "user": user,
                "sent": sent,
                "received": received,
                "seconds": round(time.monotonic() - started, 3),
            }
        )

    async def forward(
        self,
        method: str,
        target: str,
        user: str,
        lines: list[str],
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """A plain-HTTP request in absolute form (`GET http://host:port/path`), as a client sends it to
        its proxy: forwarded once, `Connection: close`. A gateway on a local network is reached so."""
        parts = urlsplit(target)
        if parts.scheme != "http" or not parts.hostname:
            self.write({"method": method, "target": target[:200], "user": user, "status": 405})
            writer.write(b"HTTP/1.1 405 Method Not Allowed\r\ncontent-length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return
        headers = [
            line
            for line in lines[1:]
            if line and line.partition(":")[0].strip().lower() not in ("proxy-authorization", "connection")
        ]
        length = next(
            (int(line.partition(":")[2]) for line in headers if line.lower().startswith("content-length:")), 0
        )
        body = await reader.readexactly(length) if length else b""
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        started = time.monotonic()
        try:
            remote_reader, remote_writer = await asyncio.open_connection(parts.hostname, parts.port or 80)
        except OSError as exc:
            self.write({"method": method, "host": parts.hostname, "user": user, "error": str(exc)[:200]})
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\ncontent-length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return
        request = "\r\n".join([f"{method} {path} HTTP/1.1", *headers, "Connection: close", "", ""])
        remote_writer.write(request.encode("latin-1") + body)
        await remote_writer.drain()
        received = await _pipe(remote_reader, writer)
        remote_writer.close()
        self.write(
            {
                "method": method,
                "host": parts.hostname,
                "port": str(parts.port or 80),
                "user": user,
                "sent": len(body),
                "received": received,
                "seconds": round(time.monotonic() - started, 3),
            }
        )


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> int:
    total = 0
    try:
        while chunk := await reader.read(65536):
            total += len(chunk)
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        with contextlib.suppress(Exception):
            writer.close()
    return total


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=3128)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    egress = Egress(args.log)
    server = await asyncio.start_server(egress.handle, "0.0.0.0", args.port)
    print(f"egress proxy on :{args.port}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
