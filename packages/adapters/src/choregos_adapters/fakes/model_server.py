# SPDX-License-Identifier: Apache-2.0
"""A scriptable chat-completions server (ADR 0005), reached through an in-memory transport.

Each request takes the next scripted reply: `say(text)` for a final answer, `call(name, arguments)`
for one tool call (several in one reply with `calls`). Past the script it answers an empty text. It
records every request — headers and body — so a test sees what left the platform, and with which key.
A reply can also be a refusal (`refuse(status, body)`), the way a gateway refuses a spent key.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class FakeModelServer:
    #: The key a request must carry (`Authorization: Bearer …`); empty: none is checked.
    key: str = ""
    script: list[dict[str, Any]] = field(default_factory=list)
    #: (headers, body) of every request received, headers lower-cased.
    requests: list[tuple[dict[str, str], dict[str, Any]]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=lambda: {"prompt_tokens": 10, "completion_tokens": 5})

    def say(self, text: str) -> FakeModelServer:
        self.script.append({"message": {"role": "assistant", "content": text}})
        return self

    def call(self, name: str, arguments: dict[str, Any] | str, *, text: str = "") -> FakeModelServer:
        return self.calls([(name, arguments)], text=text)

    def calls(self, calls: list[tuple[str, dict[str, Any] | str]], *, text: str = "") -> FakeModelServer:
        tool_calls = [
            {
                "id": f"call_{len(self.script)}_{index}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
                },
            }
            for index, (name, arguments) in enumerate(calls)
        ]
        self.script.append(
            {"message": {"role": "assistant", "content": text or None, "tool_calls": tool_calls}}
        )
        return self

    def refuse(self, status: int, body: dict[str, Any]) -> FakeModelServer:
        self.script.append({"status": status, "body": body})
        return self

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.answer)

    def answer(self, request: httpx.Request) -> httpx.Response:
        headers = {k.lower(): v for k, v in request.headers.items()}
        body = json.loads(request.content or b"{}")
        self.requests.append((headers, body))
        if not request.url.path.endswith("/chat/completions"):
            return httpx.Response(404, json={"error": {"message": f"no route {request.url.path}"}})
        if self.key and headers.get("authorization") != f"Bearer {self.key}":
            return httpx.Response(401, json={"error": {"message": "invalid key", "type": "auth_error"}})
        step: dict[str, Any] = (
            self.script.pop(0) if self.script else {"message": {"role": "assistant", "content": ""}}
        )
        if "status" in step:
            return httpx.Response(step["status"], json=step["body"])
        return httpx.Response(
            200,
            json={
                "id": f"chatcmpl-{len(self.requests)}",
                "object": "chat.completion",
                "model": body.get("model", ""),
                "choices": [{"index": 0, "message": step["message"], "finish_reason": "stop"}],
                "usage": dict(self.usage),
            },
        )
