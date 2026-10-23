# SPDX-License-Identifier: Apache-2.0
"""The guardrail on the LLM path: secrets masked in what leaves the gateway for a provider (ADR 0084).

An agent sends its model everything it read: a file holding a key, a log line with a bearer token,
a clone URL with credentials. The agent process cannot be trusted to clean that up — it is the
thing being guarded — so the masking happens at the gateway, in LiteLLM, before the provider call.

The gateway loads this file as a custom guardrail (`charts/choregos/charts/litellm/files/`, a
byte-identical copy, and the local install writes it next to LiteLLM's config). It runs on every
request but acts only for a key whose **metadata** says `choregos_llm_guard: redact`, which the
platform writes when it mints a run's key and the policy sets `sandbox.llm_security_analyzer`. A
key's metadata is the platform's, not the agent's: a request's own `metadata` is never read.

Only high-precision shapes are masked, because the model sees what is left and may write it back:
known token shapes (the `no_secrets` gate's), a long bearer or basic credential, and literal URL
user-info. `password = getpass()` stays code. The model sees `[REDACTED]` instead of the value; an
edit whose old text held that value fails, which is the price of the value never reaching the
provider. A real scanner does more; this one would rather miss than shred a prompt.

The answer is guarded on the way back too (`post_call`): a secret the model writes — one it
inferred, or read through a tool that does not go through the gateway — is masked before it
reaches the agent, and so before it reaches the run's record. A streamed answer is held line by
line, since a secret can straddle chunks.

Stdlib only: LiteLLM's image has no Choregos package. When LiteLLM is importable the class is a
`CustomGuardrail`; otherwise a plain class the tests drive the same way.
"""

from __future__ import annotations

import importlib
import json
import logging
import re
from collections.abc import AsyncIterator, Callable
from typing import Any

#: The key metadata entry the guard reads, and the one value that turns it on.
GUARD_METADATA_KEY = "choregos_llm_guard"
GUARD_REDACT = "redact"

#: What a masked value becomes (the same marker as `choregos_core.redaction`).
REDACTED = "[REDACTED]"

#: The `no_secrets` gate's shapes (`choregos_core.gates.SECRET_PATTERNS`); a test keeps them equal.
KNOWN_SHAPES: tuple[str, ...] = (
    r"AKIA[0-9A-Z]{16}",
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    r"gh[pousr]_[A-Za-z0-9]{20,}",
    r"sk-ant-[A-Za-z0-9_-]{20,}",
    r"sk-(?:proj-)?[A-Za-z0-9]{32,}",
    r"xox[baprs]-[A-Za-z0-9-]{10,}",
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
)
_SHAPES = [re.compile(pattern) for pattern in KNOWN_SHAPES]

#: `Bearer <16+ chars with a digit>`: "a basic example" and "bearer tokens" stay prose.
_SCHEME_CREDENTIALS = re.compile(r"(?i)\b(bearer|basic)\s+(?=[A-Za-z0-9._~+/=-]*\d)[A-Za-z0-9._~+/=-]{16,}")

#: `scheme://user:password@` with a literal password: `{password}` or `$(PASSWORD)` stays a template.
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[A-Za-z0-9._~%!*+-]+:[A-Za-z0-9._~%!*+-]+@")

#: The request fields a provider reads as text, across the OpenAI and Anthropic shapes.
_TEXT_FIELDS = ("messages", "system", "input", "prompt", "instructions")

#: Content blocks that carry media, not text: base64 is left alone, by block type, never by key
#: name — a tool call's `data` argument is text the model wrote.
_MEDIA_TYPES = frozenset(
    {"image", "image_url", "input_image", "document", "file", "input_file", "input_audio", "audio"}
)


def mask(text: str) -> tuple[str, int]:
    """`text` with every high-precision secret replaced by `REDACTED`, and how many were."""
    count = 0

    def _counted(replacement: str) -> Callable[[re.Match[str]], str]:
        def replace(match: re.Match[str]) -> str:
            nonlocal count
            count += 1
            return match.expand(replacement)

        return replace

    masked = _SCHEME_CREDENTIALS.sub(_counted(rf"\1 {REDACTED}"), text)
    masked = _URL_USERINFO.sub(_counted(rf"\1{REDACTED}@"), masked)
    for shape in _SHAPES:
        masked = shape.sub(_counted(REDACTED), masked)
    return masked, count


def _walk(value: Any) -> tuple[Any, int]:
    if isinstance(value, str):
        return mask(value)
    if isinstance(value, list):
        total = 0
        items = []
        for item in value:
            masked, count = _walk(item)
            items.append(masked)
            total += count
        return items, total
    if isinstance(value, dict):
        if value.get("type") in _MEDIA_TYPES:
            return value, 0
        total = 0
        out: dict[Any, Any] = {}
        for key, item in value.items():
            out[key], count = _walk(item)
            total += count
        return out, total
    return value, 0


def guard_request(data: dict[str, Any]) -> int:
    """Masks the text fields of a provider request in place; returns how many secrets it masked."""
    total = 0
    for field in _TEXT_FIELDS:
        if field in data:
            data[field], count = _walk(data[field])
            total += count
    return total


def guard_response(response: Any) -> int:
    """Masks a whole provider answer in place; returns how many secrets it masked.

    Two shapes reach the hook: LiteLLM's `ModelResponse` for `/chat/completions` (an object:
    `choices[].message.content` and its tool calls' `arguments`) and a dict for `/v1/messages`
    (Anthropic's `content` blocks: a text's `text`, a tool use's `input`).
    """
    if isinstance(response, dict):
        if "content" not in response:
            return 0
        response["content"], count = _walk(response["content"])
        return count
    total = 0
    for choice in getattr(response, "choices", None) or []:
        message = getattr(choice, "message", None)
        if message is None:
            continue
        if isinstance(getattr(message, "content", None), str):
            message.content, count = mask(message.content)
            total += count
        for call in getattr(message, "tool_calls", None) or []:
            function = getattr(call, "function", None)
            arguments = getattr(function, "arguments", None)
            if isinstance(arguments, str):
                function.arguments, count = mask(arguments)  # type: ignore[union-attr]
                total += count
    return total


#: How much text a stream may hold back without a line break before it is released at a space.
HOLD_MAX = 4096


class _Slot:
    """One string field of one held chunk: a delta's `content`, a tool call's `arguments`..."""

    def __init__(self, owner: int, text: str, write: Callable[[str], None]) -> None:
        self.owner = owner
        self.text = text
        self.write = write


class _Channel:
    """One stream of text (a choice's content, one tool call's arguments, one Anthropic block).

    A secret can straddle chunks, so the text is held until a line break — or `HOLD_MAX`
    characters, then cut at the last space — masked whole, and written back: all of it into the
    first held slot, nothing into the others. What follows the cut stays held in `carry`, and the
    last slot stays held with it, so that the rest has a slot to be released in.
    """

    def __init__(self) -> None:
        self.slots: list[_Slot] = []
        self.carry = ""
        self.masked = 0

    def _release(self, head: str, keep_last: bool) -> None:
        masked, count = mask(head)
        self.masked += count
        for index, slot in enumerate(self.slots):
            slot.write(masked if index == 0 else "")
        self.slots = [self.slots[-1]] if keep_last else []
        for slot in self.slots:
            slot.text = ""

    def add(self, slot: _Slot) -> None:
        self.slots.append(slot)
        text = self.carry + "".join(held.text for held in self.slots)
        cut = text.rfind("\n") + 1
        if not cut and len(text) > HOLD_MAX:
            cut = max(text.rfind(" "), text.rfind("\t")) + 1
        # A lone slot cut in the middle would be both the released head and the held rest.
        if not cut or (cut < len(text) and len(self.slots) < 2):
            return
        self.carry = text[cut:]
        self._release(text[:cut], keep_last=bool(self.carry))

    def close(self) -> None:
        if self.slots:
            text = self.carry + "".join(held.text for held in self.slots)
            self.carry = ""
            self._release(text, keep_last=False)

    def holds(self, owner: int) -> bool:
        return any(slot.owner == owner for slot in self.slots)


class StreamGuard:
    """Masks a streamed answer chunk by chunk, without changing the chunks' number or order.

    Each chunk is fed in; `ready()` returns, in order, the chunks no open channel still holds. A
    chunk is LiteLLM's `ModelResponseStream` (`/chat/completions`: a choice's `delta.content`, its
    tool calls' `arguments`) or SSE bytes (`/v1/messages`: a block's `text_delta` or
    `input_json_delta`). Reasoning deltas are passed through as they are.
    """

    def __init__(self) -> None:
        self._held: list[tuple[int, Any]] = []
        self._channels: dict[tuple[Any, ...], _Channel] = {}
        self._events: dict[int, list[dict[str, Any]]] = {}
        self._next = 0

    @property
    def masked(self) -> int:
        return sum(channel.masked for channel in self._channels.values())

    def _add(self, key: tuple[Any, ...], slot: _Slot) -> None:
        self._channels.setdefault(key, _Channel()).add(slot)

    def _close(self, prefix: tuple[Any, ...]) -> None:
        for key, channel in self._channels.items():
            if key[: len(prefix)] == prefix:
                channel.close()

    def feed(self, chunk: Any) -> None:
        owner = self._next
        self._next += 1
        if isinstance(chunk, bytes):
            self._feed_sse(owner, chunk)
        else:
            self._feed_object(owner, chunk)
        self._held.append((owner, chunk))

    def _feed_object(self, owner: int, chunk: Any) -> None:
        for choice in getattr(chunk, "choices", None) or []:
            index = getattr(choice, "index", 0)
            delta = getattr(choice, "delta", None)
            content = getattr(delta, "content", None)
            if isinstance(content, str) and content:

                def write(text: str, delta: Any = delta) -> None:
                    delta.content = text

                self._add(("chat", index, "content"), _Slot(owner, content, write))
            for call in getattr(delta, "tool_calls", None) or []:
                function = getattr(call, "function", None)
                arguments = getattr(function, "arguments", None)
                if isinstance(arguments, str) and arguments:

                    def write_arguments(text: str, function: Any = function) -> None:
                        function.arguments = text

                    key = ("chat", index, "tool", getattr(call, "index", 0))
                    self._add(key, _Slot(owner, arguments, write_arguments))
            if getattr(choice, "finish_reason", None):
                self._close(("chat", index))

    def _feed_sse(self, owner: int, raw: bytes) -> None:
        events: list[dict[str, Any]] = []
        for block in raw.split(b"\n\n"):
            event: dict[str, Any] = {"raw": block}
            events.append(event)
            data_lines = [line for line in block.split(b"\n") if line.startswith(b"data:")]
            if len(data_lines) != 1:
                continue
            try:
                data = json.loads(data_lines[0][5:])
            except ValueError:
                continue
            if not isinstance(data, dict):
                continue
            kind, index, delta = data.get("type"), data.get("index"), data.get("delta")
            if kind == "content_block_delta" and isinstance(delta, dict):
                field = {"text_delta": "text", "input_json_delta": "partial_json"}.get(str(delta.get("type")))
                text = delta.get(field) if field else None
                if field and isinstance(text, str) and text:

                    def write(
                        new: str,
                        event: dict[str, Any] = event,
                        data: dict[str, Any] = data,
                        field: str = field,
                    ) -> None:
                        data["delta"][field] = new
                        event["data"] = data  # re-serialized when the chunk goes out

                    self._add(("sse", index), _Slot(owner, text, write))
            elif kind == "content_block_stop":
                self._close(("sse", index))
            elif kind == "message_stop":
                self._close(("sse",))
        self._events[owner] = events

    def _render(self, owner: int, chunk: Any) -> Any:
        events = self._events.pop(owner, None)
        if events is None or not any("data" in event for event in events):
            return chunk
        blocks = []
        for event in events:
            lines = event["raw"].split(b"\n")
            if "data" in event:
                payload = b"data: " + json.dumps(event["data"]).encode()
                lines = [payload if line.startswith(b"data:") else line for line in lines]
            blocks.append(b"\n".join(lines))
        return b"\n\n".join(blocks)

    def ready(self) -> list[Any]:
        """The chunks at the head of the stream that no channel holds any more."""
        out = []
        while self._held:
            owner, chunk = self._held[0]
            if any(channel.holds(owner) for channel in self._channels.values()):
                break
            self._held.pop(0)
            out.append(self._render(owner, chunk))
        return out

    def finish(self) -> list[Any]:
        """The end of the stream: every channel is released and every chunk left goes out."""
        for channel in self._channels.values():
            channel.close()
        return self.ready()


def is_on(key_metadata: Any) -> bool:
    """Whether the key's metadata — the platform's, set at mint time — asks for the guard."""
    return isinstance(key_metadata, dict) and key_metadata.get(GUARD_METADATA_KEY) == GUARD_REDACT


_logger = logging.getLogger("choregos.llm_guard")


def _base() -> type:
    try:
        module = importlib.import_module("litellm.integrations.custom_guardrail")
    except ImportError:
        return object
    base: type = module.CustomGuardrail
    return base


_Base: Any = _base()


class ChoregosGuardrail(_Base):  # type: ignore[misc]
    """LiteLLM's guardrail (`mode: [pre_call, post_call]`), `choregos_guardrail.ChoregosGuardrail`.

    Fails closed: an error here is LiteLLM's error for the request, which never reaches the
    provider, or for the answer, which never reaches the agent.
    """

    def __init__(self, **kwargs: Any) -> None:
        if _Base is not object:
            super().__init__(**kwargs)

    async def async_pre_call_hook(
        self, user_api_key_dict: Any, cache: Any, data: dict[str, Any], call_type: Any
    ) -> Any:
        if not is_on(getattr(user_api_key_dict, "metadata", None)):
            return data
        masked = guard_request(data)
        if masked:
            # A count, never the values. Not written into the request: `metadata` reaches the provider.
            _logger.info("choregos llm guard masked %d secret(s)", masked)
        return data

    async def async_post_call_success_hook(self, data: Any, user_api_key_dict: Any, response: Any) -> Any:
        if not is_on(getattr(user_api_key_dict, "metadata", None)):
            return response
        masked = guard_response(response)
        if masked:
            _logger.info("choregos llm guard masked %d secret(s) in an answer", masked)
        return response

    async def async_post_call_streaming_iterator_hook(
        self, user_api_key_dict: Any, response: Any, request_data: Any
    ) -> AsyncIterator[Any]:
        if not is_on(getattr(user_api_key_dict, "metadata", None)):
            async for chunk in response:
                yield chunk
            return
        guard = StreamGuard()
        async for chunk in response:
            guard.feed(chunk)
            for ready in guard.ready():
                yield ready
        for ready in guard.finish():
            yield ready
        if guard.masked:
            _logger.info("choregos llm guard masked %d secret(s) in a streamed answer", guard.masked)


__all__ = [
    "GUARD_METADATA_KEY",
    "GUARD_REDACT",
    "HOLD_MAX",
    "KNOWN_SHAPES",
    "REDACTED",
    "ChoregosGuardrail",
    "StreamGuard",
    "guard_request",
    "guard_response",
    "is_on",
    "mask",
]
