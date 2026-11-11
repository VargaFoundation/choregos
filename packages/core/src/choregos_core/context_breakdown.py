# SPDX-License-Identifier: Apache-2.0
"""What fills a model call's context (ADR 0141 §3-5): sections estimated, scaled to the exact total.

A request body (the chat-completions shape, and the Anthropic `messages` and OpenAI `responses` shapes
a hosted container may send) is split into sections: `system`, `tools.platform`, `tools.mcp`, `skills`,
`knowledge`, `memory`, `messages` and `backend` (what the platform did not write). Each section is
estimated with the platform's one counter (`estimate_tokens`, chars/4), then scaled so the sections
sum **exactly** to the `prompt_tokens` the model reported (largest-remainder rounding): the split is an
estimate, the total is not.

What leaves this module is numbers: section sizes, the total, the model, the call's ordinal. Never a
message, a tool schema or a digest of either (ADR 0141 §5).

`ContextMeter` is what a builder holds for one run or one response: it numbers the calls, makes the
breakdown, and keeps at most one event per `WINDOW_S` seconds — the run's first call, the largest call
of each window, and its last call.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from .domain import utcnow
from .models import estimate_tokens

SECTIONS: tuple[str, ...] = (
    "system",
    "tools.platform",
    "tools.mcp",
    "skills",
    "knowledge",
    "memory",
    "messages",
    "backend",
)
#: The separator of a tool the platform relays from an MCP server: `server__tool`.
MCP_SEPARATOR = "__"
#: At most one `context.breakdown` event per window, per run (ADR 0141 §5).
WINDOW_S = 5.0
EVENT = "context.breakdown"

Source = Literal["loop", "hosted_proxy", "gateway"]
Record = Callable[[str, dict[str, Any]], Awaitable[None]]

_SYSTEM_ROLES = frozenset({"system", "developer"})
#: Where a message's text lives, in the three shapes; images and files are not text and not counted.
_TEXT_KEYS = ("text", "content", "output", "thinking")
_JSON_KEYS = ("arguments", "input")


@dataclass(frozen=True)
class Hints:
    """What the builder knows of its own request.

    `parts`: text the builder wrote, by section (the playbook as `system`, the skills index as
    `skills`, a knowledge pack as `knowledge`); where a message holds one, its size counts in that
    section and not in the message's. `foreign`: the request was built by someone else (a hosted
    container): what no part and no known MCP server explains is `backend`, not `system` or
    `messages`. `mcp_servers`: in a foreign request, the servers whose `server__tool` names count as
    `tools.mcp`.
    """

    parts: Mapping[str, Iterable[str]] = field(default_factory=dict)
    foreign: bool = False
    mcp_servers: frozenset[str] = frozenset()


def tool_section(name: str, hints: Hints | None = None) -> str:
    """`tools.mcp` for a tool relayed from an MCP server (`server__tool`), `tools.platform` for one the
    platform's builder offers itself; in a foreign request, a tool of no known server is `backend`."""
    hints = hints or Hints()
    if not hints.foreign:
        return "tools.mcp" if MCP_SEPARATOR in name else "tools.platform"
    segments = name.split(MCP_SEPARATOR)
    if len(segments) > 1 and any(segment in hints.mcp_servers for segment in segments[:-1]):
        return "tools.mcp"
    return "backend"


def estimate_sections(body: Mapping[str, Any], hints: Hints | None = None) -> dict[str, int]:
    """Each section's size by `estimate_tokens`, before scaling. Zero for a section with nothing."""
    hints = hints or Hints()
    parts = sorted(
        ((section, text) for section, texts in hints.parts.items() for text in texts if text),
        key=lambda pair: len(pair[1]),
        reverse=True,
    )
    sizes = dict.fromkeys(SECTIONS, 0)
    for tool in body.get("tools") or []:
        if isinstance(tool, dict):
            sizes[tool_section(_tool_name(tool), hints)] += estimate_tokens(json.dumps(tool))
    own = "backend" if hints.foreign else "system"
    for text in _texts(body.get("system")), _texts(body.get("instructions")):
        _count(text, own, parts, sizes)
    messages = body.get("messages")
    if messages is None:
        messages = body.get("input")
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    for message in messages if isinstance(messages, list) else []:
        if isinstance(message, dict):
            _count(_message_text(message), _section_of(message, hints), parts, sizes)
    return sizes


def scale(estimates: Mapping[str, int], total: int, *, fallback: str = "messages") -> dict[str, int]:
    """The estimates scaled so that they sum exactly to `total`, by largest remainder (ties in
    `SECTIONS` order). Nothing estimated puts the whole total in `fallback`."""
    sized = {section: max(0, int(estimates.get(section, 0))) for section in SECTIONS}
    scaled = dict.fromkeys(SECTIONS, 0)
    if total <= 0:
        return scaled
    weight = sum(sized.values())
    if weight == 0:
        scaled[fallback] = total
        return scaled
    remainders: list[tuple[int, int, str]] = []
    for order, section in enumerate(SECTIONS):
        share, remainder = divmod(sized[section] * total, weight)
        scaled[section] = share
        remainders.append((-remainder, order, section))
    left = total - sum(scaled.values())
    for _, _, section in sorted(remainders)[:left]:
        scaled[section] += 1
    return scaled


def prompt_tokens_of(usage: Any) -> int | None:
    """The call's input tokens as the model reported them, cache included: OpenAI's `prompt_tokens`,
    the Responses API's `input_tokens`, Anthropic's `input_tokens` plus its cache reads and writes.
    `None` when the usage says nothing: the total is never guessed."""
    if not isinstance(usage, dict):
        return None
    if isinstance(usage.get("prompt_tokens"), int):
        return int(usage["prompt_tokens"])
    if isinstance(usage.get("input_tokens"), int):
        cached = (usage.get(key) for key in ("cache_read_input_tokens", "cache_creation_input_tokens"))
        return int(usage["input_tokens"]) + sum(value for value in cached if isinstance(value, int))
    return None


def breakdown(
    body: Mapping[str, Any],
    prompt_tokens: int,
    *,
    model: str,
    call: int,
    source: Source,
    max_input_tokens: int | None = None,
    hints: Hints | None = None,
) -> dict[str, Any]:
    """One call's `ContextBreakdown` (the contract's shape): numbers only."""
    return payload(
        estimate_sections(body, hints),
        prompt_tokens,
        model=model,
        call=call,
        source=source,
        max_input_tokens=max_input_tokens,
        foreign=bool(hints and hints.foreign),
    )


def payload(
    estimates: Mapping[str, int],
    prompt_tokens: int,
    *,
    model: str,
    call: int,
    source: Source,
    max_input_tokens: int | None = None,
    foreign: bool = False,
) -> dict[str, Any]:
    return {
        "call": call,
        "at": utcnow().isoformat(),
        "model": model,
        "prompt_tokens": prompt_tokens,
        "max_input_tokens": max_input_tokens,
        "source": source,
        "sections": scale(estimates, prompt_tokens, fallback="backend" if foreign else "messages"),
    }


class Throttle:
    """At most one breakdown per window: the first call at once, then the largest call of each window
    when the window is over, and on `close` the last call too (ADR 0141 §5)."""

    def __init__(self, window_s: float = WINDOW_S) -> None:
        self.window_s = window_s
        self._started: float | None = None
        self._largest: dict[str, Any] | None = None
        self._last: dict[str, Any] | None = None

    def offer(self, item: dict[str, Any], now: float) -> list[dict[str, Any]]:
        """What to emit now that `item` was measured at `now`."""
        if self._started is None:
            self._started = now
            return [item]
        kept: list[dict[str, Any]] = []
        if now - self._started >= self.window_s:
            if self._largest is not None:
                kept.append(self._largest)
            self._started, self._largest = now, None
        if self._largest is None or item["prompt_tokens"] > self._largest["prompt_tokens"]:
            self._largest = item
        self._last = item
        return kept

    def close(self) -> list[dict[str, Any]]:
        """What is still held: the open window's largest, and the last call when it is another one."""
        held = [item for item in (self._largest, self._last) if item is not None]
        unique = {id(item): item for item in held}
        self._started, self._largest, self._last = None, None, None
        return sorted(unique.values(), key=lambda item: int(item["call"]))


class ContextMeter:
    """The breakdowns of one run's (or one response's) calls, recorded as `context.breakdown` events."""

    def __init__(
        self,
        record: Record,
        *,
        model: str,
        source: Source,
        max_input_tokens: int | None = None,
        hints: Hints | None = None,
        first_call: int = 1,
        window_s: float = WINDOW_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.record = record
        self.model = model
        self.source: Source = source
        self.max_input_tokens = max_input_tokens
        self.hints = hints or Hints()
        self.calls = first_call - 1
        self.clock = clock
        self.throttle = Throttle(window_s)

    def estimate(self, body: Mapping[str, Any]) -> dict[str, int]:
        """The sections of a request about to leave: kept instead of the request while it is answered."""
        return estimate_sections(body, self.hints)

    async def observe(self, body: Mapping[str, Any], usage: Any) -> None:
        """One call answered: `body` is what was sent, `usage` what the model reported."""
        await self.observe_estimates(self.estimate(body), usage)

    async def observe_estimates(self, estimates: Mapping[str, int], usage: Any) -> None:
        self.calls += 1
        total = prompt_tokens_of(usage)
        if total is None:
            return
        item = payload(
            estimates,
            total,
            model=self.model,
            call=self.calls,
            source=self.source,
            max_input_tokens=self.max_input_tokens,
            foreign=self.hints.foreign,
        )
        for kept in self.throttle.offer(item, self.clock()):
            await self.record(EVENT, kept)

    async def close(self) -> None:
        for kept in self.throttle.close():
            await self.record(EVENT, kept)


def _tool_name(tool: Mapping[str, Any]) -> str:
    function = tool.get("function")
    if isinstance(function, dict) and isinstance(function.get("name"), str):
        return str(function["name"])
    name = tool.get("name")
    return name if isinstance(name, str) else ""


def _section_of(message: Mapping[str, Any], hints: Hints) -> str:
    role = message.get("role")
    if role in _SYSTEM_ROLES:
        return "backend" if hints.foreign else "system"
    if hints.foreign and role == "user":
        # What a container sends as the person's turn is its own wrapping of the platform's task:
        # the parts the platform wrote are counted out of it below, the rest is the container's.
        return "backend"
    return "messages"


def _count(text: str, section: str, parts: list[tuple[str, str]], sizes: dict[str, int]) -> None:
    """`text` in `section`, except the parts the builder recognises, which count in their own."""
    if not text:
        return
    for part_section, part in parts:
        found = text.count(part)
        if found:
            sizes[part_section] += found * estimate_tokens(part)
            text = text.replace(part, "")
    if text.strip():
        sizes[section] += estimate_tokens(text)


def _message_text(message: Mapping[str, Any]) -> str:
    texts = [_texts(message.get(key)) for key in _TEXT_KEYS]
    texts += [_json(message.get(key)) for key in _JSON_KEYS]
    for call in message.get("tool_calls") or []:
        if isinstance(call, dict) and isinstance(call.get("function"), dict):
            texts.append(_message_text(call["function"]))
    name = message.get("name")
    if isinstance(name, str) and message.get("type") in {"function_call", "tool_use"}:
        texts.append(name)
    return "\n".join(text for text in texts if text)


def _texts(value: Any) -> str:
    """The text a content holds: a string, a list of blocks, a block."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(text for text in (_texts(item) for item in value) if text)
    if isinstance(value, dict):
        return _message_text(value)
    return ""


def _json(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict | list) and value:
        return json.dumps(value)
    return ""
