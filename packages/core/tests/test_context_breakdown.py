"""What fills a model call's context (ADR 0141 §3-5): sections estimated, scaled to the exact total.

The total is the model's: the sections sum to the reported `prompt_tokens`, whatever the estimates. An
MCP tool (`server__tool`) counts as `tools.mcp` and a tool the builder offers itself as
`tools.platform`. What leaves is numbers: no message text, no tool schema. And a run keeps its first
call, the largest of each 5-second window, and its last call.
"""

from __future__ import annotations

import json
import random
from typing import Any

import pytest
from choregos_core.context_breakdown import (
    EVENT,
    SECTIONS,
    ContextMeter,
    Hints,
    Throttle,
    breakdown,
    estimate_sections,
    prompt_tokens_of,
    scale,
)
from choregos_core.prompt_loop import LoopRequest, LoopTool, run_loop

CANARY = "canary-7f3a-the-person-said-this"


def _tool(name: str, description: str = "A tool.") -> dict[str, Any]:
    return LoopTool(name, description, {"type": "object", "properties": {"q": {"type": "string"}}}).spec()


def _body() -> dict[str, Any]:
    return {
        "model": "platform/standard",
        "messages": [
            {"role": "system", "content": "You carry one step. " * 20},
            {"role": "user", "content": f"Fix the total. {CANARY}"},
        ],
        "tools": [
            _tool("read_file"),
            _tool("submit_result"),
            _tool("crm__lookup", "Look up a customer " * 30),
        ],
    }


def test_the_sections_sum_exactly_to_the_reported_prompt_tokens() -> None:
    result = breakdown(_body(), 4321, model="m", call=1, source="loop")

    assert sum(result["sections"].values()) == 4321
    assert set(result["sections"]) == set(SECTIONS)


def test_any_estimates_scale_to_any_total() -> None:
    """A property, swept on seeded draws: the scaled sections always sum to the total, never go
    negative, and a section estimated at zero stays at zero when anything else was estimated."""
    draws = random.Random(141)
    for _ in range(2000):
        estimates = {section: draws.choice([0, 0, 1, draws.randint(0, 10**6)]) for section in SECTIONS}
        total = draws.choice([0, 1, 7, draws.randint(0, 2 * 10**6)])
        scaled = scale(estimates, total)

        assert sum(scaled.values()) == total
        assert all(value >= 0 for value in scaled.values())
        if any(estimates.values()):
            assert all(scaled[s] == 0 for s, estimate in estimates.items() if estimate == 0)


def test_largest_remainder_rounding() -> None:
    assert scale({"system": 1, "messages": 1, "skills": 1}, 10) == {
        **dict.fromkeys(SECTIONS, 0),
        "system": 4,
        "skills": 3,
        "messages": 3,
    }
    assert scale({}, 50)["messages"] == 50, "nothing estimated: the total is still the total"
    assert scale({}, 50, fallback="backend")["backend"] == 50


def test_an_mcp_tool_counts_in_tools_mcp_and_a_runner_tool_in_tools_platform() -> None:
    only_mcp = estimate_sections({"tools": [_tool("crm__lookup")]})
    only_runner = estimate_sections({"tools": [_tool("read_file")]})

    assert only_mcp["tools.mcp"] > 0 and only_mcp["tools.platform"] == 0
    assert only_runner["tools.platform"] > 0 and only_runner["tools.mcp"] == 0


def test_no_text_leaves_the_breakdown() -> None:
    result = breakdown(_body(), 900, model="m", call=3, source="loop")

    serialised = json.dumps(result)
    assert CANARY not in serialised
    assert "Look up a customer" not in serialised and "read_file" not in serialised
    assert set(result) == {"call", "at", "model", "prompt_tokens", "max_input_tokens", "source", "sections"}


def test_the_parts_the_builder_wrote_count_in_their_own_section() -> None:
    skills = "## Skills\n- pdf: read a PDF\n" * 10
    knowledge = "## Knowledge\n- the handbook\n" * 40
    body = {"messages": [{"role": "user", "content": f"Playbook.\n\n{skills}\n\n{knowledge}\n\nThe task."}]}
    hints = Hints(parts={"skills": [skills], "knowledge": [knowledge]})

    sizes = estimate_sections(body, hints)

    assert sizes["knowledge"] > sizes["skills"] > sizes["messages"] > 0
    assert estimate_sections(body)["skills"] == 0, "without the hint, it is the message's"


def test_a_foreign_request_puts_what_the_platform_did_not_write_in_backend() -> None:
    task = "Fix the total of invoice 42."
    hints = Hints(parts={"messages": [task]}, foreign=True, mcp_servers=frozenset({"crm"}))
    body = {
        "system": "You are a helpful container agent. " * 10,
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": f"<task>{task}</task>"}]},
            {"role": "assistant", "content": [{"type": "tool_use", "name": "x", "input": {"a": 1}}]},
        ],
        "tools": [
            {"name": "crm__lookup", "description": "Look up.", "input_schema": {"type": "object"}},
            {"name": "bash", "description": "Run a command.", "input_schema": {"type": "object"}},
        ],
    }

    sizes = estimate_sections(body, hints)

    assert sizes["backend"] > 0 and sizes["system"] == 0, "the container's own prompt is backend"
    assert sizes["tools.mcp"] > 0 and sizes["tools.platform"] == 0
    assert sizes["messages"] > 0, "the task the platform wrote, and the conversation"


def test_the_responses_shape_is_read() -> None:
    body = {
        "instructions": "Be brief.",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "Hello there."}]}],
        "tools": [{"type": "function", "name": "crm__lookup", "parameters": {}}],
    }
    sizes = estimate_sections(body)
    assert sizes["system"] > 0 and sizes["messages"] > 0 and sizes["tools.mcp"] > 0


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        ({"prompt_tokens": 120, "completion_tokens": 5}, 120),
        ({"input_tokens": 100, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 50}, 1050),
        ({"input_tokens": 77, "output_tokens": 3}, 77),
        ({"completion_tokens": 5}, None),
        ({}, None),
        (None, None),
    ],
)
def test_the_total_is_the_models_and_never_guessed(usage: Any, expected: int | None) -> None:
    assert prompt_tokens_of(usage) == expected


def _item(call: int, tokens: int) -> dict[str, Any]:
    return {"call": call, "prompt_tokens": tokens}


def test_the_throttle_keeps_the_first_the_largest_of_each_window_and_the_last() -> None:
    throttle = Throttle(window_s=5)
    emitted: list[int] = []
    # Window 1 opens at 0: call 1 is emitted at once; 2-4 fall in it, 3 is the largest.
    # Call 5 at t=6 closes it (3 goes out) and opens window 2: 5-7, the largest is 6, the last is 7.
    for call, (at, tokens) in enumerate(
        [(0, 100), (1, 150), (2, 900), (4, 300), (6, 400), (7, 950), (8, 500)], start=1
    ):
        emitted += [item["call"] for item in throttle.offer(_item(call, tokens), at)]
    emitted += [item["call"] for item in throttle.close()]

    assert emitted == [1, 3, 6, 7]


def test_a_single_call_is_kept_once() -> None:
    throttle = Throttle()
    assert [i["call"] for i in throttle.offer(_item(1, 10), 0)] == [1]
    assert throttle.close() == []


def test_three_hundred_calls_keep_at_most_one_per_window_plus_the_last() -> None:
    throttle = Throttle(window_s=5)
    kept: list[dict[str, Any]] = []
    for call in range(1, 301):
        kept += throttle.offer(_item(call, call % 37), call * 0.5)  # a call every half second
    kept += throttle.close()

    windows = 150 / 5
    assert len(kept) <= windows + 2
    assert kept[0]["call"] == 1 and kept[-1]["call"] == 300


@pytest.mark.asyncio
async def test_the_meter_records_numbers_only_and_nothing_without_a_usage() -> None:
    recorded: list[tuple[str, dict[str, Any]]] = []

    async def record(type_: str, payload: dict[str, Any]) -> None:
        recorded.append((type_, payload))

    now = [0.0]
    meter = ContextMeter(record, model="platform/standard", source="loop", clock=lambda: now[0])
    await meter.observe(_body(), {"prompt_tokens": 800})
    now[0] = 1
    await meter.observe(_body(), {"completion_tokens": 3})  # no input count: nothing, never a guess
    now[0] = 2
    await meter.observe(_body(), {"prompt_tokens": 1200})
    await meter.close()

    assert [type_ for type_, _ in recorded] == [EVENT, EVENT]
    assert [(p["call"], p["prompt_tokens"]) for _, p in recorded] == [(1, 800), (3, 1200)]
    assert all(sum(p["sections"].values()) == p["prompt_tokens"] for _, p in recorded)
    assert all(p["max_input_tokens"] is None and p["source"] == "loop" for _, p in recorded)
    assert CANARY not in json.dumps(recorded)


@pytest.mark.asyncio
async def test_the_loop_keeps_each_calls_prompt_tokens_and_tells_the_caller() -> None:
    answers = iter(
        [
            {
                "choices": [
                    {"message": {"tool_calls": [{"id": "1", "function": {"name": "t", "arguments": "{}"}}]}}
                ],
                "usage": {"prompt_tokens": 50},
            },
            {"choices": [{"message": {"content": "done"}}], "usage": {"prompt_tokens": 90}},
        ]
    )
    seen: list[tuple[int, int]] = []

    async def complete(body: dict[str, Any]) -> dict[str, Any]:
        return next(answers)

    async def call_tool(name: str, arguments: dict[str, Any]) -> str:
        return "ok"

    async def on_call(body: dict[str, Any], usage: dict[str, Any]) -> None:
        seen.append((len(body["messages"]), usage["prompt_tokens"]))

    outcome = await run_loop(
        LoopRequest(model="m", max_turns=3),
        [{"role": "user", "content": "go"}],
        [LoopTool("t", "", {"type": "object"})],
        complete,
        call_tool,
        on_call=on_call,
    )

    assert outcome.prompt_tokens == [50, 90]
    assert seen == [(1, 50), (3, 90)], "each call is told with the request it sent"
