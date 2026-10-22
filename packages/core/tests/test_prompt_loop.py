"""A prompt agent's tool loop (ADR 0080), against a scripted model."""

from __future__ import annotations

import json
from typing import Any

import pytest
from choregos_core.prompt_loop import LoopOutcome, LoopRequest, LoopTool, run_loop

pytestmark = pytest.mark.asyncio

LOOKUP = LoopTool(
    "lookup", "Look a ticket up.", {"type": "object", "properties": {"key": {"type": "string"}}}
)


def _model(*replies: dict[str, Any]) -> tuple[Any, list[dict[str, Any]]]:
    script, bodies = list(replies), []

    async def complete(body: dict[str, Any]) -> dict[str, Any]:
        bodies.append(json.loads(json.dumps(body)))
        message = script.pop(0) if script else {"role": "assistant", "content": ""}
        return {"choices": [{"message": message}], "usage": {"prompt_tokens": 3, "completion_tokens": 2}}

    return complete, bodies


async def _loop(
    complete: Any,
    call_tool: Any,
    *,
    messages: list[dict[str, Any]] | None = None,
    max_turns: int = 5,
    tool_choice: Any = "auto",
    max_output_tokens: int | None = None,
    response_format: Any = "text",
    **hooks: Any,
) -> LoopOutcome:
    request = LoopRequest("m", max_turns, max_output_tokens, tool_choice, response_format)
    return await run_loop(
        request, [] if messages is None else messages, [LOOKUP], complete, call_tool, **hooks
    )


def _call(name: str, arguments: dict[str, Any] | str = "{}", call_id: str = "c1") -> dict[str, Any]:
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": raw}}],
    }


def _say(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": text}


async def test_the_tool_answer_reaches_the_model_and_its_text_ends_the_loop() -> None:
    complete, bodies = _model(_call("lookup", {"key": "A-1"}), _say("A-1 is open."))
    called: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(name: str, arguments: dict[str, Any]) -> str:
        called.append((name, arguments))
        return "A-1: open"

    outcome = await _loop(
        complete,
        call_tool,
        messages=[{"role": "user", "content": "Is A-1 open?"}],
        max_turns=5,
    )
    assert (outcome.status, outcome.text, outcome.turns, outcome.tool_calls) == (
        "final",
        "A-1 is open.",
        2,
        1,
    )
    assert called == [("lookup", {"key": "A-1"})]
    assert bodies[1]["messages"][-1] == {"role": "tool", "tool_call_id": "c1", "content": "A-1: open"}
    assert bodies[0]["tools"][0]["function"]["name"] == "lookup"
    assert outcome.usage == {"prompt_tokens": 6, "completion_tokens": 4}


async def test_the_loop_stops_at_max_turns() -> None:
    complete, bodies = _model(*[_call("lookup", {"key": "A-1"}, f"c{n}") for n in range(10)])

    async def call_tool(name: str, arguments: dict[str, Any]) -> str:
        return "again"

    outcome = await _loop(complete, call_tool, messages=[], max_turns=3)
    assert outcome.status == "max_turns"
    assert outcome.turns == 3 and len(bodies) == 3, "no fourth model call"


async def test_a_tool_the_model_invents_is_never_called() -> None:
    complete, bodies = _model(_call("delete_everything", {"all": True}), _say("Done."))
    called: list[str] = []
    events: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(name: str, arguments: dict[str, Any]) -> str:
        called.append(name)
        return ""

    async def on_event(type_: str, payload: dict[str, Any]) -> None:
        events.append((type_, payload))

    outcome = await _loop(
        complete,
        call_tool,
        messages=[],
        max_turns=5,
        on_event=on_event,
    )
    assert called == [] and outcome.refused == ["delete_everything"] and outcome.tool_calls == 0
    assert "no tool named `delete_everything`" in bodies[1]["messages"][-1]["content"]
    assert ("prompt.tool_refused", {"tool": "delete_everything", "reason": "not offered"}) in events


async def test_unreadable_arguments_go_back_to_the_model() -> None:
    complete, bodies = _model(_call("lookup", "{not json"), _say("Sorry."))

    async def call_tool(name: str, arguments: dict[str, Any]) -> str:
        raise AssertionError("never called")

    await _loop(complete, call_tool, messages=[], max_turns=5)
    assert "not valid JSON" in bodies[1]["messages"][-1]["content"]


async def test_stop_when_ends_the_loop_after_the_tool_answer() -> None:
    complete, bodies = _model(_call("lookup", {"key": "A-1"}), _say("never asked"))
    done: list[bool] = []

    async def call_tool(name: str, arguments: dict[str, Any]) -> str:
        done.append(True)
        return "ok"

    outcome = await _loop(
        complete,
        call_tool,
        messages=[],
        max_turns=5,
        stop_when=lambda: bool(done),
    )
    assert outcome.status == "stopped" and len(bodies) == 1


async def test_on_final_sends_the_model_back_and_its_turns_count() -> None:
    complete, bodies = _model(_say("I think so."), _say("Still text."))
    reminded: list[str] = []

    async def on_final(text: str) -> str | None:
        reminded.append(text)
        return "Submit your result." if len(reminded) == 1 else None

    async def call_tool(name: str, arguments: dict[str, Any]) -> str:
        return ""

    outcome = await _loop(
        complete,
        call_tool,
        messages=[],
        max_turns=5,
        on_final=on_final,
        tool_choice="required",
        max_output_tokens=50,
        response_format="json_object",
    )
    assert outcome.status == "final" and outcome.turns == 2
    assert bodies[1]["messages"][-1] == {"role": "user", "content": "Submit your result."}
    assert bodies[0]["tool_choice"] == "required" and bodies[0]["max_tokens"] == 50
    assert bodies[0]["response_format"] == {"type": "json_object"}


async def test_tool_choice_none_offers_no_tool() -> None:
    complete, bodies = _model(_say("Hello."))

    async def call_tool(name: str, arguments: dict[str, Any]) -> str:
        return ""

    await _loop(
        complete,
        call_tool,
        messages=[],
        max_turns=1,
        tool_choice="none",
    )
    assert "tools" not in bodies[0]
