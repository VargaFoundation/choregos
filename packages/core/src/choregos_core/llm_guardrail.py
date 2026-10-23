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
reaches the agent, and so before it reaches the run's record. Every string of the answer is read,
whatever the route (`/chat/completions`, `/v1/messages`, `/v1/responses`) and whatever the field:
the text, a tool call's arguments, the model's reasoning (`reasoning_content`, thinking blocks, a
reasoning summary). What the provider signs or encrypts is left as it is (a thinking block's
`signature`, a reasoning item's `encrypted_content`, a redacted thinking block): it carries no
text, and a changed one is refused when the agent sends it back. A thinking block whose text was
masked is no longer the block the provider signed: it goes out **without its signature**, streamed
or not, rather than with one that no longer matches (ADR 0084 §6). Sent back as it is, the provider
refuses it for the missing signature, which is visible, where the secret would not have been.

A streamed answer is held line by line where a secret can straddle chunks: a choice's content,
reasoning and refusal, a tool call's arguments (a legacy `function_call`'s too), an Anthropic
block's text, JSON or thinking, a Responses event's `delta`. Each chunk is then masked whole on its
way out, for the fields no channel holds.

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

#: Blocks the provider produced opaque: an encrypted thinking, a generated image, a signature.
_OPAQUE_TYPES = _MEDIA_TYPES | {
    "redacted_thinking",
    "signature_delta",
    "image_generation_call",
    "image_generation.partial_image",
}

#: The provider's signed or encrypted fields, by the type of the block that holds them — never by
#: key name alone: a tool call's `signature` argument is text the model wrote.
_OPAQUE_FIELDS: dict[str, frozenset[str]] = {
    "thinking": frozenset({"signature"}),
    "reasoning": frozenset({"encrypted_content"}),
}


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


def _kind(value: Any) -> str | None:
    """A block's or an event's `type`, LiteLLM's enums included (they are `str` subclasses)."""
    value = getattr(value, "value", value)
    return value if isinstance(value, str) else None


def _field(block: Any, name: str) -> Any:
    """A block's field, whether LiteLLM hands it over as a dict or as an object."""
    return block.get(name) if isinstance(block, dict) else getattr(block, name, None)


def _walk(value: Any) -> tuple[Any, int]:
    """`value` with its strings masked: lists and dicts rebuilt, any other object in place.

    The objects are LiteLLM's (pydantic models: their fields and extras) or the tests' namespaces;
    a name with a leading underscore is the object's own state, not what the client receives.
    """
    if isinstance(value, str):
        return mask(value)
    if value is None or isinstance(value, (bool, int, float, bytes)):
        return value, 0
    if isinstance(value, list):
        total = 0
        items = []
        for item in value:
            masked, count = _walk(item)
            items.append(masked)
            total += count
        return items, total
    if isinstance(value, dict):
        return _walk_dict(value)
    return _walk_object(value)


def _walk_dict(value: dict[Any, Any]) -> tuple[Any, int]:
    kind = _kind(value.get("type"))
    if kind in _OPAQUE_TYPES:
        return value, 0
    opaque = _OPAQUE_FIELDS.get(kind or "", frozenset())
    total = 0
    out: dict[Any, Any] = {}
    for key, item in value.items():
        if key in opaque:
            out[key] = item
            continue
        out[key], count = _walk(item)
        total += count
    if kind == "thinking" and total:
        out.pop("signature", None)  # masked: no longer the block the provider signed
    return out, total


def _walk_object(value: Any) -> tuple[Any, int]:
    kind = _kind(getattr(value, "type", None))
    if kind in _OPAQUE_TYPES:
        return value, 0
    opaque = _OPAQUE_FIELDS.get(kind or "", frozenset())
    fields = dict(vars(value)) if hasattr(value, "__dict__") else {}
    extra = getattr(value, "__pydantic_extra__", None)
    if isinstance(extra, dict):
        fields.update(extra)
    total = 0
    for name, item in fields.items():
        if name.startswith("_") or name in opaque:
            continue
        masked, count = _walk(item)
        if count:
            setattr(value, name, masked)
            total += count
    if kind == "thinking" and total and getattr(value, "signature", None) is not None:
        value.signature = None  # masked: no longer the block the provider signed
    return value, total


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

    Three shapes reach the hook: LiteLLM's `ModelResponse` for `/chat/completions` (an object:
    `choices[].message` with its content, reasoning, thinking blocks and tool calls), a dict for
    `/v1/messages` (Anthropic's `content` blocks) and LiteLLM's `ResponsesAPIResponse` for
    `/v1/responses` (an object: `output[]` with its messages, reasoning and function calls). Every
    string is read, so a field this list forgot is masked all the same.
    """
    if isinstance(response, dict):
        masked, count = _walk(response)
        if count:
            response.clear()
            response.update(masked)
        return count
    return int(_walk(response)[1])


#: How much text a stream may hold back without a line break before it is released at a space.
HOLD_MAX = 4096

#: An Anthropic `content_block_delta`'s text field, by delta type; `signature_delta` is opaque.
_SSE_DELTA_FIELDS = {"text_delta": "text", "input_json_delta": "partial_json", "thinking_delta": "thinking"}


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
    `reasoning_content` and thinking blocks, its tool calls' `arguments`), SSE bytes
    (`/v1/messages`: a block's `text_delta`, `input_json_delta` or `thinking_delta`) or a Responses
    event (`/v1/responses`: any `response.*.delta`, closed by its `.done`). Once no channel holds
    it, a chunk is masked whole: the `.done` events and the final response repeat the text whole.
    """

    def __init__(self) -> None:
        self._held: list[tuple[int, Any]] = []
        self._channels: dict[tuple[Any, ...], _Channel] = {}
        self._events: dict[int, list[dict[str, Any]]] = {}
        #: A chunk's signatures, with the channel of the thinking they sign: dropped on the way out
        #: when that channel masked anything.
        self._signed: dict[int, list[tuple[tuple[Any, ...], Callable[[], None]]]] = {}
        self._next = 0
        self._whole = 0
        self.unsigned = 0

    @property
    def masked(self) -> int:
        return self._whole + sum(channel.masked for channel in self._channels.values())

    def _add(self, key: tuple[Any, ...], slot: _Slot) -> None:
        self._channels.setdefault(key, _Channel()).add(slot)

    def _sign(self, owner: int, key: tuple[Any, ...], unsign: Callable[[], None]) -> None:
        self._signed.setdefault(owner, []).append((key, unsign))

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
        kind = _kind(getattr(chunk, "type", None))
        if kind and kind.startswith("response."):
            self._feed_responses(owner, chunk, kind)
            return
        for choice in getattr(chunk, "choices", None) or []:
            index = getattr(choice, "index", 0)
            delta = getattr(choice, "delta", None)
            for field in ("content", "reasoning_content", "refusal"):
                text = getattr(delta, field, None)
                if isinstance(text, str) and text:

                    def write(new: str, delta: Any = delta, field: str = field) -> None:
                        setattr(delta, field, new)

                    self._add(("chat", index, field), _Slot(owner, text, write))
            for number, block in enumerate(getattr(delta, "thinking_blocks", None) or []):
                self._feed_thinking(owner, ("chat", index, "thinking", number), block)
            calls: list[tuple[tuple[Any, ...], Any]] = [
                (("chat", index, "function_call"), getattr(delta, "function_call", None))
            ]
            calls += [
                (("chat", index, "tool", getattr(call, "index", 0)), getattr(call, "function", None))
                for call in getattr(delta, "tool_calls", None) or []
            ]
            for key, function in calls:
                arguments = getattr(function, "arguments", None)
                if isinstance(arguments, str) and arguments:

                    def write_arguments(text: str, function: Any = function) -> None:
                        function.arguments = text

                    self._add(key, _Slot(owner, arguments, write_arguments))
            if getattr(choice, "finish_reason", None):
                self._close(("chat", index))

    def _feed_thinking(self, owner: int, key: tuple[Any, ...], block: Any) -> None:
        """A chat delta's thinking block: its text is a channel, its signature waits on it."""
        thinking, signature = _field(block, "thinking"), _field(block, "signature")
        if isinstance(thinking, str) and thinking:

            def write(new: str, block: Any = block) -> None:
                if isinstance(block, dict):
                    block["thinking"] = new
                else:
                    block.thinking = new

            self._add(key, _Slot(owner, thinking, write))
        if signature:

            def unsign(block: Any = block) -> None:
                if isinstance(block, dict):
                    block.pop("signature", None)
                else:
                    block.signature = None

            self._sign(owner, key, unsign)

    def _feed_responses(self, owner: int, event: Any, kind: str) -> None:
        output = getattr(event, "output_index", None)
        delta = getattr(event, "delta", None)
        if kind.endswith(".delta") and isinstance(delta, str):
            if delta:

                def write(new: str, event: Any = event) -> None:
                    event.delta = new

                where = (getattr(event, "content_index", None), getattr(event, "summary_index", None))
                self._add(("responses", output, *where, kind), _Slot(owner, delta, write))
        elif kind.endswith(".done"):
            self._close(("responses", output))
        elif kind in {"response.completed", "response.incomplete", "response.failed"}:
            self._close(("responses",))

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
            event["data"] = data
            kind, index, delta = data.get("type"), data.get("index"), data.get("delta")
            if kind == "content_block_delta" and isinstance(delta, dict):
                field = _SSE_DELTA_FIELDS.get(str(delta.get("type")))
                text = delta.get(field) if field else None
                if field and isinstance(text, str) and text:

                    def write(
                        new: str,
                        event: dict[str, Any] = event,
                        data: dict[str, Any] = data,
                        field: str = field,
                    ) -> None:
                        data["delta"][field] = new
                        event["written"] = True  # re-serialized when the chunk goes out

                    self._add(("sse", index), _Slot(owner, text, write))
                elif delta.get("type") == "signature_delta":

                    def drop(event: dict[str, Any] = event) -> None:
                        event["dropped"] = True

                    self._sign(owner, ("sse", index), drop)
            elif kind == "content_block_stop":
                self._close(("sse", index))
            elif kind == "message_stop":
                self._close(("sse",))
        self._events[owner] = events

    def _render(self, owner: int, chunk: Any) -> Any:
        """The chunk on its way out, masked whole: what no channel held is masked here."""
        for key, unsign in self._signed.pop(owner, []):
            channel = self._channels.get(key)
            if channel is not None and channel.masked:
                unsign()
                self.unsigned += 1
        events = self._events.pop(owner, None)
        if events is None:
            self._whole += _walk(chunk)[1]
            return chunk
        for event in events:
            if "data" in event:
                event["data"], count = _walk(event["data"])
                self._whole += count
                event["written"] = event.get("written", False) or bool(count)
        if not any(event.get("written") or event.get("dropped") for event in events):
            return chunk
        blocks = []
        for event in events:
            if event.get("dropped"):
                continue  # the signature of a thinking block the guard masked
            lines = event["raw"].split(b"\n")
            if event.get("written"):
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
            _logger.info(
                "choregos llm guard masked %d secret(s) in a streamed answer, dropped %d signature(s)",
                guard.masked,
                guard.unsigned,
            )


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
