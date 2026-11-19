"""S4-08: the platform's own model calls carry a stable, cacheable prefix (ADR 0189)."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from tranibor_core.prompt_cache import MAX_BREAKPOINTS, anthropic_family, cache_counters, with_prompt_cache
from tranibor_core.prompt_loop import LoopRequest, LoopTool

SYSTEM = "You are the Choregos assistant. " + "Follow the house rules. " * 40
CLAUDE = "anthropic/claude-sonnet-5"
EPHEMERAL = {"type": "ephemeral"}


def _tool(name: str) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}


def _body(model: str, question: str, system: str = SYSTEM) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": question}],
        "tools": [_tool("zeta"), _tool("alpha")],
    }


def _prefix(body: dict[str, Any]) -> str:
    """Everything the provider caches: the tools, then the leading system blocks."""
    system = [m for m in body["messages"] if m["role"] == "system"]
    return json.dumps({"tools": body["tools"], "system": system}, sort_keys=True)


def test_breakpoints_sit_on_the_last_tool_and_the_last_system_block() -> None:
    body = with_prompt_cache(_body(CLAUDE, "q"))
    assert [t["function"]["name"] for t in body["tools"]] == ["alpha", "zeta"], "tools sorted by name"
    assert body["tools"][-1]["cache_control"] == EPHEMERAL
    assert "cache_control" not in body["tools"][0]
    system, user = body["messages"]
    assert system["content"] == [{"type": "text", "text": SYSTEM, "cache_control": EPHEMERAL}]
    assert user["content"] == "q", "the volatile question stays after the breakpoint, unmarked"


def test_the_prefix_is_identical_across_two_calls_with_different_volatile_input() -> None:
    first = with_prompt_cache(_body(CLAUDE, "What is the status of run 7?"))
    second = with_prompt_cache(_body(CLAUDE, "Summarise item 42 at 2026-11-18T10:00Z"))
    assert _prefix(first) == _prefix(second)
    assert first["messages"][1] != second["messages"][1]


def test_a_timestamp_in_the_system_prompt_breaks_the_prefix() -> None:
    """The guard itself: a stamp in the prefix makes two calls differ (the test above would go red)."""
    stamped = [
        with_prompt_cache(_body(CLAUDE, "q", f"{SYSTEM}\nNow: {datetime(2026, 11, 18, 10, m, tzinfo=UTC)}"))
        for m in (0, 1)
    ]
    assert _prefix(stamped[0]) != _prefix(stamped[1])


def test_tool_order_does_not_move_the_prefix() -> None:
    one, two = _body(CLAUDE, "q"), _body(CLAUDE, "q")
    two["tools"].reverse()
    assert _prefix(with_prompt_cache(one)) == _prefix(with_prompt_cache(two))


@pytest.mark.parametrize("model", ["platform/cheap", "openai/gpt-5", "gemini/gemini-3-pro", "gpt-4o"])
def test_other_providers_get_no_cache_field(model: str) -> None:
    body = with_prompt_cache(_body(model, "q"))
    assert "cache_control" not in json.dumps(body)
    assert body["messages"][0]["content"] == SYSTEM, "the system text is left as a plain string"


def test_a_gateway_alias_is_cached_only_when_the_deployment_names_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHOREGOS_PROMPT_CACHE_MODELS", raising=False)
    assert "cache_control" not in json.dumps(with_prompt_cache(_body("platform/standard", "q")))
    monkeypatch.setenv("CHOREGOS_PROMPT_CACHE_MODELS", "platform/strong, platform/standard")
    assert "cache_control" in json.dumps(with_prompt_cache(_body("platform/standard", "q")))
    assert anthropic_family("platform/standard", aliases=[]) is False


@pytest.mark.parametrize(
    "model", [CLAUDE, "bedrock/anthropic.claude-3", "vertex_ai/claude-haiku-4-5", "claude-opus-5"]
)
def test_anthropic_family_names(model: str) -> None:
    assert anthropic_family(model, aliases=[])


def test_the_callers_body_is_not_modified_and_a_second_pass_adds_nothing() -> None:
    original = _body(CLAUDE, "q")
    snapshot = copy.deepcopy(original)
    once = with_prompt_cache(original)
    assert original == snapshot
    assert with_prompt_cache(once) == once


def test_never_more_than_four_breakpoints() -> None:
    body = _body(CLAUDE, "q")
    marked = [{"type": "text", "text": f"t{i}", "cache_control": EPHEMERAL} for i in range(4)]
    body["messages"].insert(1, {"role": "assistant", "content": marked})
    assert json.dumps(with_prompt_cache(body)).count("cache_control") == MAX_BREAKPOINTS


def test_a_later_system_message_is_volatile_and_not_marked() -> None:
    body = _body(CLAUDE, "q")
    body["messages"].append({"role": "system", "content": "citation rule for this turn"})
    assert with_prompt_cache(body)["messages"][-1]["content"] == "citation rule for this turn"


def test_the_prompt_loop_builds_its_body_with_the_breakpoints() -> None:
    tool = LoopTool(name="zeta", description="d", parameters={"type": "object"})
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "q"}]
    body = LoopRequest(model=CLAUDE, max_turns=2).body(messages, [tool])
    assert body["messages"][0]["content"][0]["cache_control"] == EPHEMERAL
    assert body["tools"][-1]["cache_control"] == EPHEMERAL
    assert messages[0]["content"] == SYSTEM, "the loop's own history stays plain"
    plain = LoopRequest(model="openai/gpt-5", max_turns=2).body(messages, [tool])
    assert "cache_control" not in json.dumps(plain)


def test_cache_counters_read_both_spellings() -> None:
    anthropic = cache_counters({"cache_read_input_tokens": 90, "cache_creation_input_tokens": 10})
    assert anthropic == {"cache_read_tokens": 90, "cache_write_tokens": 10}
    assert cache_counters({"prompt_tokens_details": {"cached_tokens": 5}})["cache_read_tokens"] == 5
    assert cache_counters(None) == {"cache_read_tokens": 0, "cache_write_tokens": 0}
