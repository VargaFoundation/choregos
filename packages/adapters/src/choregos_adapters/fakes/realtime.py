# SPDX-License-Identifier: Apache-2.0
"""A scriptable realtime model behind the gateway (ADR 0005, ADR 0080 §10), in memory.

It speaks the OpenAI Realtime events a voice session relays. `say(text)` scripts the next
response: an audio delta and its transcript; `call(name, arguments)` a function call. A
`response.create` plays the next scripted response; an `input_audio_buffer.commit` stands for the
person having spoken: it answers the transcript `hear(text)` scripted and, as server VAD would,
plays the next response. `usage` is what each `response.done` reports.

Everything the relay sends is kept in `received` (parsed), with the url and headers of the connection,
so a test sees what reached the model and with which key.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from ..base import RealtimeClosed

#: The audio a scripted response "speaks": base64 of a few PCM16 samples, recognisable in a journal.
AUDIO_B64 = "AAABAAIAAwAEAAUABgAHAAgACQ=="


@dataclass
class FakeRealtimeUpstream:
    script: list[list[dict[str, Any]]] = field(default_factory=list)
    heard: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=lambda: {"input_tokens": 120, "output_tokens": 40})
    received: list[dict[str, Any]] = field(default_factory=list)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    connections: int = 0
    closed: bool = False
    _out: asyncio.Queue[str | None] = field(default_factory=asyncio.Queue)
    _responses: int = 0

    # ───────────────────────── the script ─────────────────────────

    def say(self, text: str) -> FakeRealtimeUpstream:
        item = f"item_{len(self.script)}"
        self.script.append(
            [
                {"type": "response.output_audio.delta", "item_id": item, "delta": AUDIO_B64},
                {"type": "response.output_audio_transcript.delta", "item_id": item, "delta": text},
                {"type": "response.output_audio_transcript.done", "item_id": item, "transcript": text},
            ]
        )
        return self

    def call(self, name: str, arguments: dict[str, Any] | str) -> FakeRealtimeUpstream:
        self.script.append(
            [
                {
                    "type": "response.function_call_arguments.done",
                    "call_id": f"call_{len(self.script)}",
                    "name": name,
                    "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
                }
            ]
        )
        return self

    def hear(self, text: str) -> FakeRealtimeUpstream:
        self.heard.append(text)
        return self

    def refuse(self, message: str) -> FakeRealtimeUpstream:
        """The next response is an `error` event, then the link closes: a gateway refusing the key."""
        error = {"type": "invalid_request_error", "message": message}
        self.script.append([{"type": "error", "error": error}])
        return self

    def sent(self, type_: str) -> list[dict[str, Any]]:
        return [event for event in self.received if event.get("type") == type_]

    # ───────────────────────── the connector ─────────────────────────

    async def connect(self, url: str, headers: dict[str, str]) -> FakeRealtimeUpstream:
        self.url, self.headers = url, {k.lower(): v for k, v in headers.items()}
        self.connections += 1
        self.closed = False
        self._emit({"type": "session.created", "session": {"type": "realtime"}})
        return self

    # ───────────────────────── the link ─────────────────────────

    async def send(self, message: str) -> None:
        if self.closed:
            raise RealtimeClosed(1006, "the link is closed")
        event = json.loads(message)
        self.received.append(event)
        type_ = event.get("type")
        if type_ == "session.update":
            self._emit({"type": "session.updated", "session": event.get("session", {})})
        elif type_ == "conversation.item.create":
            self._emit({"type": "conversation.item.created", "item": event.get("item", {})})
        elif type_ == "input_audio_buffer.commit":
            self._emit({"type": "input_audio_buffer.committed"})
            if self.heard:
                transcript = self.heard.pop(0)
                type_ = "conversation.item.input_audio_transcription.completed"
                self._emit({"type": type_, "transcript": transcript})
            self._play()
        elif type_ == "response.create":
            self._play()

    async def recv(self) -> str:
        message = await self._out.get()
        if message is None:
            raise RealtimeClosed(1000, "upstream closed")
        return message

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._out.put_nowait(None)

    def _emit(self, event: dict[str, Any]) -> None:
        self._out.put_nowait(json.dumps(event))

    def _play(self) -> None:
        self._responses += 1
        response_id = f"resp_{self._responses}"
        events = self.script.pop(0) if self.script else []
        if events and events[0]["type"] == "error":
            self._emit(events[0])
            self.closed = True
            self._out.put_nowait(None)
            return
        self._emit({"type": "response.created", "response": {"id": response_id}})
        for event in events:
            self._emit({**event, "response_id": response_id})
        done = {"id": response_id, "status": "completed", "usage": self.usage}
        self._emit({"type": "response.done", "response": done})
