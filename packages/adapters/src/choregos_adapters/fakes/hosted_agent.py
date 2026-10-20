# SPDX-License-Identifier: Apache-2.0
"""A scriptable hosted agent (ADR 0005, ADR 0080): Foundry's container contract, in memory.

The runner reaches it through `transport()`; it reaches the platform the way a real container does,
over real loopback sockets, with nothing but its environment: `OPENAI_BASE_URL` and the loopback key,
`CHOREGOS_MCP_URL`, `HTTPS_PROXY`. Each `POST /responses` (or `/invocations`) plays the script —
model calls, MCP tool calls, tunnels — and answers `answer`, streamed as Responses events. It is
ready after `ready_after` readiness probes; `None` never.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx


@dataclass
class FakeHostedAgent:
    env: dict[str, str] = field(default_factory=dict)
    #: ("model", text) | ("mcp", server, tool, arguments) | ("tunnel", host, port) | ("direct", url)
    script: list[tuple[Any, ...]] = field(default_factory=list)
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

    async def _play(self, action: tuple[Any, ...]) -> Any:
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

    async def _tunnel(self, host: str, port: int) -> str:
        proxy = urlsplit(self.env["HTTPS_PROXY"])
        reader, writer = await asyncio.open_connection(proxy.hostname, proxy.port)
        writer.write(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
        await writer.drain()
        line = (await reader.readline()).decode("latin-1").strip()
        writer.close()
        return line


def _rpc(id_: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params}
