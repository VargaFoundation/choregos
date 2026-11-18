# SPDX-License-Identifier: Apache-2.0
"""A streamed chat completion (S63-04b): the gateway's `stream: true`, read as it arrives.

`stream_completion` posts the request with `stream: true` and `stream_options.include_usage`, hands each
text chunk to `on_delta` the moment it is read, and returns the completion the prompt loop expects (the
same shape as a non-streamed answer: `choices[0].message` with the whole text and the assembled tool
calls, and `usage`). So the loop is unchanged: every model call of a turn streams, whether it ends in
text or in tool calls, and the tool calls stay journaled by the loop as they happen.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

OnDelta = Callable[[str], Awaitable[None]]


class StreamRefused(Exception):  # noqa: N818 - the model's refusal, with the status the gateway gave
    def __init__(self, status: int, text: str) -> None:
        super().__init__(f"model answered {status}: {text[:500]}")
        self.status, self.text = status, text


async def stream_completion(
    http: httpx.AsyncClient, url: str, body: dict[str, Any], headers: dict[str, str], on_delta: OnDelta
) -> dict[str, Any]:
    request = {**body, "stream": True, "stream_options": {"include_usage": True}}
    text: list[str] = []
    calls: dict[int, dict[str, Any]] = {}
    usage: dict[str, Any] = {}
    async with http.stream("POST", url, json=request, headers=headers) as answer:
        if answer.status_code != 200:
            raise StreamRefused(answer.status_code, (await answer.aread()).decode("utf-8", "replace"))
        async for line in answer.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line.removeprefix("data:").strip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(event.get("usage"), dict):
                usage = event["usage"]
            for choice in event.get("choices") or []:
                delta = choice.get("delta") or {}
                if isinstance(delta.get("content"), str) and delta["content"]:
                    text.append(delta["content"])
                    await on_delta(delta["content"])
                for part in delta.get("tool_calls") or []:
                    call = calls.setdefault(
                        int(part.get("index", 0)),
                        {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                    )
                    call["id"] = part.get("id") or call["id"]
                    function = part.get("function") or {}
                    call["function"]["name"] += function.get("name") or ""
                    call["function"]["arguments"] += function.get("arguments") or ""
    message: dict[str, Any] = {"role": "assistant", "content": "".join(text) or None}
    if calls:
        message["tool_calls"] = [calls[index] for index in sorted(calls)]
    return {
        "object": "chat.completion",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": usage,
    }
