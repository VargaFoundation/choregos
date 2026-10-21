# SPDX-License-Identifier: Apache-2.0
"""A scriptable hosted agent (ADR 0005, ADR 0080): Foundry's container contract, in memory.

The runner reaches it through `transport()`; it reaches the platform the way a real container does,
over real loopback sockets, with nothing but its environment: `OPENAI_BASE_URL` and the loopback key,
`CHOREGOS_MCP_URL`, `HTTPS_PROXY`. Each `POST /responses` (or `/invocations`) plays the script —
model calls, MCP tool calls, tunnels — and answers `answer`, streamed as Responses events. It is
ready after `ready_after` readiness probes; `None` never.

With `workspace` (S41-18), the shared copy of the working tree the container sees at `/workspace`:
the actions `read`, `write`, `delete` and `symlink` work in it as a container's code would.

It serves `/invocations_ws` too (S41-21): `connect` is a `RealtimeConnector`. Each text frame plays
the script, then is answered `{"type": "echo", "text": …}`; `socket_greeting` is sent when the
socket opens, and `socket_close` closes it after `socket_close_after` frames.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..base import RealtimeClosed


@dataclass
class FakeHostedAgent:
    env: dict[str, str] = field(default_factory=dict)
    #: ("model", text) | ("mcp", server, tool, arguments) | ("tunnel", host, port) | ("direct", url)
    #: | ("read", path) | ("write", path, text) | ("delete", path) | ("symlink", path, target)
    #: | ("fifo", path)
    script: list[tuple[Any, ...]] = field(default_factory=list)
    #: The shared working tree, where the executor mounts it; `None` for `workspace: none`.
    workspace: Path | None = None
    #: The final answer: a text, or a JSON object (sent as its JSON text).
    answer: str | dict[str, Any] = "Done."
    ready_after: int | None = 0
    #: `failed`: the stream ends with `response.failed`.
    fail: bool = False
    stream: bool = True
    probes: int = 0
    requests: list[dict[str, Any]] = field(default_factory=list)
    #: What each scripted action got back, in order.
    seen: list[Any] = field(default_factory=list)
    #: `/invocations_ws`: the frames sent when it opens, and the close it answers with — (code,
    #: reason) after `socket_close_after` frames; `socket_refused` refuses the connection.
    socket_greeting: list[str] = field(default_factory=list)
    socket_close: tuple[int, str] | None = None
    socket_close_after: int = 0
    socket_refused: bool = False
    socket_urls: list[str] = field(default_factory=list)
    socket_heard: list[str] = field(default_factory=list)
    sockets: list[FakeHostedSocket] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    async def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/readiness":
            self.probes += 1
            ready = self.ready_after is not None and self.probes > self.ready_after
            return httpx.Response(200 if ready else 503)
        if request.method != "POST" or request.url.path not in ("/responses", "/invocations"):
            return httpx.Response(404)
        self.requests.append(json.loads(request.content or b"{}"))
        for action in self.script:
            self.seen.append(await self._play(action))
        text = self.answer if isinstance(self.answer, str) else json.dumps(self.answer)
        if request.url.path == "/invocations":
            return httpx.Response(200, json={"output": text})
        response: dict[str, Any] = {
            "id": "resp_1",
            "object": "response",
            "status": "failed" if self.fail else "completed",
            "output": [
                {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}
            ],
            **({"error": {"code": "server_error", "message": "the agent crashed"}} if self.fail else {}),
        }
        if not self.stream:
            return httpx.Response(200, json=response)
        events: list[dict[str, Any]] = [
            {"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
            {"type": "response.output_text.delta", "delta": text},
            {"type": "response.failed" if self.fail else "response.completed", "response": response},
        ]
        body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())

    async def connect(self, url: str, headers: dict[str, str]) -> FakeHostedSocket:
        """`/invocations_ws`, as `RealtimeConnector.connect`."""
        self.socket_urls.append(url)
        if self.socket_refused:
            raise ConnectionRefusedError("the agent refused the socket")
        link = FakeHostedSocket(self)
        self.sockets.append(link)
        for text in self.socket_greeting:
            link.out.put_nowait(text)
        if self.socket_close is not None and self.socket_close_after == 0:
            link.out.put_nowait(self.socket_close)
        return link

    async def _play(self, action: tuple[Any, ...]) -> Any:
        if action[0] in ("read", "write", "delete", "symlink", "fifo"):
            return self._in_workspace(action)
        async with httpx.AsyncClient(trust_env=False, timeout=30) as http:
            if action[0] == "model":
                answer = await http.post(
                    f"{self.env['OPENAI_BASE_URL']}/chat/completions",
                    headers={"authorization": f"Bearer {self.env['OPENAI_API_KEY']}"},
                    json={"model": "gpt-4o", "messages": [{"role": "user", "content": action[1]}]},
                )
                return answer.status_code, answer.json()
            if action[0] == "mcp":
                _, server, tool, arguments = action
                url = f"{self.env['CHOREGOS_MCP_URL']}{server}"
                headers = {"accept": "application/json, text/event-stream"}
                hello = await http.post(url, headers=headers, json=_rpc(0, "initialize", {}))
                if hello.status_code != 200:
                    return hello.status_code, hello.json()
                if session := hello.headers.get("mcp-session-id"):
                    headers["mcp-session-id"] = session
                call = {"name": tool, "arguments": arguments}
                answer = await http.post(url, headers=headers, json=_rpc(1, "tools/call", call))
                return answer.status_code, answer.json()
            if action[0] == "tunnel":
                return await self._tunnel(str(action[1]), int(action[2]))
            if action[0] == "direct":
                try:
                    await http.get(str(action[1]), timeout=2)
                except httpx.HTTPError as exc:
                    return f"unreachable: {type(exc).__name__}"
                return "reached"
        raise ValueError(f"unknown action {action[0]!r}")

    def _in_workspace(self, action: tuple[Any, ...]) -> Any:
        if self.workspace is None:
            return "no workspace"
        target = self.workspace / str(action[1])
        if action[0] == "read":
            return target.read_text() if target.is_file() else None
        if action[0] == "delete":
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
            return "deleted"
        target.parent.mkdir(parents=True, exist_ok=True)
        if action[0] == "symlink":
            target.symlink_to(str(action[2]))
            return "linked"
        if action[0] == "fifo":
            os.mkfifo(target)
            return "made"
        target.write_text(str(action[2]))
        return "written"

    async def _tunnel(self, host: str, port: int) -> str:
        proxy = urlsplit(self.env["HTTPS_PROXY"])
        reader, writer = await asyncio.open_connection(proxy.hostname, proxy.port)
        writer.write(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
        await writer.drain()
        line = (await reader.readline()).decode("latin-1").strip()
        writer.close()
        return line


class FakeHostedSocket:
    """One open `/invocations_ws` of a `FakeHostedAgent`."""

    def __init__(self, agent: FakeHostedAgent) -> None:
        self.agent = agent
        self.out: asyncio.Queue[str | tuple[int, str]] = asyncio.Queue()
        self.closed = False

    async def send(self, message: str) -> None:
        if self.closed:
            raise RealtimeClosed(1000)
        self.agent.socket_heard.append(message)
        for action in self.agent.script:
            self.agent.seen.append(await self.agent._play(action))
        self.out.put_nowait(json.dumps({"type": "echo", "text": message}))
        close = self.agent.socket_close
        if close is not None and len(self.agent.socket_heard) == self.agent.socket_close_after:
            self.out.put_nowait(close)

    async def recv(self) -> str:
        message = await self.out.get()
        if isinstance(message, tuple):
            self.closed = True
            raise RealtimeClosed(*message)
        return message

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.out.put_nowait((1000, ""))


def _rpc(id_: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params}
