# SPDX-License-Identifier: Apache-2.0
"""A scriptable fake MCP server (ADR 0005): an ASGI application that the client reaches through an
in-memory transport. It serves its tools by pages, answers in JSON or SSE, requires a session
after `initialize`, and records every request, to check what leaves the platform.

A tool returns a text (`answers`), or DOES something (`gestures`): a function called with its
arguments, whose object comes back in `structuredContent` and as JSON text, for a client that does
not read it. A refusal (`AdapterError`) comes back as an `isError` result, as MCP wants for a tool
error: it is not a protocol error."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..errors import AdapterError


@dataclass
class FakeMcpServer:
    tools: list[dict[str, Any]] = field(default_factory=list)
    #: the expected token; empty: none
    token: str = ""
    page_size: int = 2
    sse: bool = False
    #: (method, headers, body) of every request received
    received: list[tuple[str, dict[str, str], dict[str, Any]]] = field(default_factory=list)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    session: str = "session-1"
    #: the text a tool returns; by default "<tool> done"
    answers: dict[str, str] = field(default_factory=dict)
    #: what a tool does, when it does something
    gestures: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = field(default_factory=dict)
    #: a tool's `structuredContent`, sent beside its text and free to say what the text does not
    structured: dict[str, Any] = field(default_factory=dict)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.respond)

    def respond(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        headers = {k.lower(): v for k, v in request.headers.items()}
        method = str(body.get("method", ""))
        self.received.append((method, headers, body))
        if self.token and headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"error": "unauthorized"})
        if "id" not in body:
            return httpx.Response(202)
        if method != "initialize" and headers.get("mcp-session-id") != self.session:
            return httpx.Response(400, json={"error": "missing session"})
        result = self._result(method, body.get("params") or {})
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": body["id"]}
        if isinstance(result, tuple):
            message["error"] = {"code": result[0], "message": result[1]}
        else:
            message["result"] = result
        response_headers = {"Mcp-Session-Id": self.session} if method == "initialize" else {}
        if self.sse:
            text = 'event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n'
            text += f"event: message\ndata: {json.dumps(message)}\n\n"
            return httpx.Response(
                200, text=text, headers={"Content-Type": "text/event-stream", **response_headers}
            )
        return httpx.Response(200, json=message, headers=response_headers)

    def _result(self, method: str, params: dict[str, Any]) -> dict[str, Any] | tuple[int, str]:
        if method == "initialize":
            return {
                "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-mcp", "version": "1"},
            }
        if method == "tools/list":
            start = int(params.get("cursor") or 0)
            page = self.tools[start : start + self.page_size]
            following = start + self.page_size
            return {"tools": page, **({"nextCursor": str(following)} if following < len(self.tools) else {})}
        if method == "tools/call":
            name = str(params.get("name"))
            # A gesture is callable even when not announced: a tool kept under its former name.
            if name not in self.gestures and not any(o["name"] == name for o in self.tools):
                return (-32602, f"unknown tool: {name}")
            arguments = dict(params.get("arguments") or {})
            self.calls.append((name, arguments))
            gesture = self.gestures.get(name)
            if gesture is not None:
                try:
                    produced = gesture(arguments)
                except AdapterError as refusal:
                    return {"content": [{"type": "text", "text": str(refusal)}], "isError": True}
                return {
                    "content": [{"type": "text", "text": json.dumps(produced, ensure_ascii=False)}],
                    "structuredContent": produced,
                    "isError": False,
                }
            text = self.answers.get(name, f"{name} done")
            answer: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": False}
            if name in self.structured:
                answer["structuredContent"] = self.structured[name]
            return answer
        return (-32601, f"unknown method: {method}")
