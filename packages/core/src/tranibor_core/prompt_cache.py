# SPDX-License-Identifier: Apache-2.0
"""Prompt caching for the platform's own model calls (S4-08, ADR 0189).

The platform calls models through the LiteLLM gateway on `/chat/completions`. A system prompt, an
instruction text and a tool list repeat on every call; unmarked, they are billed at full input price
each time. This module makes the request body cache-friendly in two steps:

1. **A stable prefix** (every provider): the tools sorted by name, the leading `system` messages
   first, volatile data (the question, run ids, timestamps, per-item content) after them. OpenAI and
   the other automatic caches need nothing more than a prefix that does not move.
2. **A breakpoint** (Anthropic family only): `cache_control: {"type": "ephemeral"}` on the last tool
   and on the last leading system block. Anthropic reads the prefix in the order tools, system,
   messages, allows 4 breakpoints at most (this module uses 2) and caches a prefix only above a
   per-model minimum (1024 to 4096 tokens: below it the marker is harmless and does nothing). The
   cache lives 5 minutes, refreshed on each read.

LiteLLM v1.105.5 (the gateway image, `charts/choregos/charts/litellm/values.yaml`) accepts
`cache_control` on a message content block and on a tool, and strips it for providers that do not
know it; the body still carries it for an Anthropic model only, so a gateway that is not LiteLLM, or
another version, never sees a field it may refuse.

The gateway's answer says what was cached: `usage.cache_read_input_tokens` /
`cache_creation_input_tokens` (Anthropic) or `usage.prompt_tokens_details.cached_tokens` (OpenAI);
`cache_counters` reads them. Persisting them is the ledger's business (S4-07), not this module's.
"""

from __future__ import annotations

import copy
import os
from collections.abc import Iterable, Mapping
from typing import Any, Final

EPHEMERAL: Final = {"type": "ephemeral"}
#: Anthropic refuses a request with more than four breakpoints. This module places at most two and
#: leaves the rest to the caller (a marker already present is kept and counted).
MAX_BREAKPOINTS: Final = 4
#: Gateway aliases (`platform/standard`) that name an Anthropic upstream, comma separated: an alias
#: says nothing about its provider, so the deployment says it.
ALIASES_ENV: Final = "CHOREGOS_PROMPT_CACHE_MODELS"
_FAMILY_MARKERS: Final = ("anthropic/", "claude", "anthropic.")


def configured_aliases(environ: Mapping[str, str] | None = None) -> frozenset[str]:
    raw = (environ if environ is not None else os.environ).get(ALIASES_ENV, "")
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def anthropic_family(model: object, aliases: Iterable[str] | None = None) -> bool:
    """Whether `model` is an Anthropic-family model: `anthropic/...`, `bedrock/anthropic....`,
    `vertex_ai/claude-...`, a bare `claude-...`, or an alias the deployment lists."""
    if not isinstance(model, str) or not model:
        return False
    listed = set(aliases) if aliases is not None else configured_aliases()
    if model in listed:
        return True
    lowered = model.lower()
    return any(marker in lowered for marker in _FAMILY_MARKERS)


def _tool_name(tool: Any) -> str:
    function = tool.get("function") if isinstance(tool, dict) else None
    return str(function.get("name", "")) if isinstance(function, dict) else ""


def _count_breakpoints(body: Mapping[str, Any]) -> int:
    count = sum(1 for tool in body.get("tools") or [] if isinstance(tool, dict) and "cache_control" in tool)
    for message in body.get("messages") or []:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            count += sum(1 for block in content if isinstance(block, dict) and "cache_control" in block)
    return count


def _leading_system(messages: list[Any]) -> int:
    """How many `system` messages open the conversation (system messages met later are volatile)."""
    end = 0
    while end < len(messages) and isinstance(messages[end], dict) and messages[end].get("role") == "system":
        end += 1
    return end


def _mark_message(message: dict[str, Any]) -> None:
    content = message.get("content")
    if isinstance(content, str):
        if content:
            message["content"] = [{"type": "text", "text": content, "cache_control": dict(EPHEMERAL)}]
    elif isinstance(content, list) and content and isinstance(content[-1], dict):
        content[-1].setdefault("cache_control", dict(EPHEMERAL))


def with_prompt_cache(body: Mapping[str, Any], aliases: Iterable[str] | None = None) -> dict[str, Any]:
    """A copy of `body` with a stable prefix and, for an Anthropic model, the two breakpoints.

    The caller's body is never changed (the prompt loop extends its `messages` in place). Tools are
    sorted by name for every provider; the breakpoints are placed only for an Anthropic-family model
    and only up to `MAX_BREAKPOINTS` in all. Idempotent: a second pass adds nothing."""
    prepared: dict[str, Any] = copy.deepcopy(dict(body))
    tools = prepared.get("tools")
    if isinstance(tools, list) and tools:
        prepared["tools"] = sorted(tools, key=_tool_name)
    if not anthropic_family(prepared.get("model"), aliases):
        return prepared
    messages = prepared.get("messages")
    leading = _leading_system(messages) if isinstance(messages, list) else 0
    if isinstance(messages, list) and leading and _count_breakpoints(prepared) < MAX_BREAKPOINTS:
        _mark_message(messages[leading - 1])
    tools = prepared.get("tools")
    if isinstance(tools, list) and tools and _count_breakpoints(prepared) < MAX_BREAKPOINTS:
        last = tools[-1]
        if isinstance(last, dict):
            last.setdefault("cache_control", dict(EPHEMERAL))
    return prepared


def cache_counters(usage: Mapping[str, Any] | None) -> dict[str, int]:
    """What the gateway's `usage` says was read from or written to the cache, across the providers'
    spellings (0 when absent). Read-only: the ledger's split of these is S4-07."""
    usage = usage or {}
    details = usage.get("prompt_tokens_details")
    details = details if isinstance(details, Mapping) else {}

    def number(value: Any) -> int:
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    return {
        "cache_read_tokens": number(usage.get("cache_read_input_tokens"))
        or number(details.get("cached_tokens")),
        "cache_write_tokens": number(usage.get("cache_creation_input_tokens")),
    }
