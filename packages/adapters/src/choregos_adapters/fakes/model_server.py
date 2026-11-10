# SPDX-License-Identifier: Apache-2.0
"""A scriptable chat-completions server (ADR 0005), reached through an in-memory transport.

Each request takes the next scripted reply: `say(text)` for a final answer, `call(name, arguments)`
for one tool call (several in one reply with `calls`). Past the script it answers an empty text. It
records every request — headers and body — so a test sees what left the platform, and with which key.
`say_chunks(*parts)` is a final answer a client that asked `stream: true` receives as one server-sent
chunk per part (any other reply streams as one text chunk, a tool call as its name then its arguments).
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

    def say_chunks(self, *parts: str) -> FakeModelServer:
        message = {"role": "assistant", "content": "".join(parts)}
        self.script.append({"message": message, "chunks": list(parts)})
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
        if body.get("stream"):
            return self._stream(step, body)
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

    def _stream(self, step: dict[str, Any], body: dict[str, Any]) -> httpx.Response:
        """The step as `chat.completion.chunk` server-sent events, ending with usage and `[DONE]`."""
        ident, model = f"chatcmpl-{len(self.requests)}", body.get("model", "")

        def event(payload: dict[str, Any]) -> str:
            base = {"id": ident, "object": "chat.completion.chunk", "model": model}
            return f"data: {json.dumps({**base, **payload})}\n\n"

        def chunk(delta: dict[str, Any], finish: str | None = None) -> str:
            return event({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})

        message = step["message"]
        events = [chunk({"role": "assistant"})]
        for part in step.get("chunks") or ([message["content"]] if message.get("content") else []):
            events.append(chunk({"content": part}))
        for index, call in enumerate(message.get("tool_calls") or []):
            function = call["function"]
            head = {"index": index, "id": call["id"], "type": "function"}
            named = {"name": function["name"], "arguments": ""}
            events.append(chunk({"tool_calls": [{**head, "function": named}]}))
            arguments = function["arguments"]
            middle = len(arguments) // 2
            for piece in (arguments[:middle], arguments[middle:]):
                events.append(chunk({"tool_calls": [{"index": index, "function": {"arguments": piece}}]}))
        events.append(chunk({}, "stop"))
        if (body.get("stream_options") or {}).get("include_usage"):
            events.append(event({"choices": [], "usage": dict(self.usage)}))
        events.append("data: [DONE]\n\n")
        headers = {"content-type": "text/event-stream"}
        return httpx.Response(200, headers=headers, content="".join(events).encode())
