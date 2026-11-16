# SPDX-License-Identifier: Apache-2.0
"""A prompt agent's tool loop (ADR 0080): the model, the tools it was offered, and nothing else.

The loop owns no transport. The caller hands it `complete` (one chat-completions call, request body in,
response body out) and `call_tool` (one offered tool, arguments in, text out), so a workflow step and a
session run the same loop, and a test scripts both. Every model call is a turn; past `max_turns` the
loop stops on its own. A tool the model names that it was not offered is never called: the refusal goes
back to the model as the tool's answer, and to `on_event`.

Each call's `prompt_tokens` is kept (`LoopOutcome.prompt_tokens`), and `on_call` hears every call with
the request it sent and the usage it got back: what a caller measures the context with (ADR 0141 §3).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

Complete = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
CallTool = Callable[[str, dict[str, Any]], Awaitable[str]]
OnEvent = Callable[[str, dict[str, Any]], Awaitable[None]]
#: Told of every model call: the request body sent, and the `usage` the answer carried (`{}` if none).
OnCall = Callable[[dict[str, Any], dict[str, Any]], Awaitable[None]]
#: Asked when the model ends its turn with text: a message that sends it back to work, or `None`.
OnFinal = Callable[[str], Awaitable[str | None]]

#: What the journal keeps of a tool's arguments and answer.
_EXCERPT = 500


@dataclass(frozen=True)
class LoopTool:
    """A tool offered to the model, in the chat-completions `function` shape."""

    name: str
    description: str
    parameters: dict[str, Any]

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


@dataclass
class LoopOutcome:
    """How the loop ended: `final` (the model answered), `stopped` (`stop_when` said so), or
    `max_turns` (the loop stopped the model)."""

    status: Literal["final", "stopped", "max_turns"]
    text: str = ""
    turns: int = 0
    tool_calls: int = 0
    refused: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    #: Each call's `prompt_tokens`, in order (`None` for a call whose answer did not say).
    prompt_tokens: list[int | None] = field(default_factory=list)


@dataclass(frozen=True)
class LoopRequest:
    """What every model call of the loop asks for."""

    model: str
    max_turns: int
    max_output_tokens: int | None = None
    tool_choice: Literal["auto", "required", "none"] = "auto"
    response_format: Literal["text", "json_object"] = "text"

    def body(self, messages: list[dict[str, Any]], tools: list[LoopTool]) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self.model, "messages": messages}
        if tools and self.tool_choice != "none":
            body["tools"] = [tool.spec() for tool in tools]
            body["tool_choice"] = self.tool_choice
        if self.max_output_tokens is not None:
            body["max_tokens"] = self.max_output_tokens
        if self.response_format == "json_object":
            body["response_format"] = {"type": "json_object"}
        return body


async def run_loop(  # noqa: PLR0913 - the loop and its four hooks
    request: LoopRequest,
    messages: list[dict[str, Any]],
    tools: list[LoopTool],
    complete: Complete,
    call_tool: CallTool,
    *,
    on_event: OnEvent | None = None,
    on_final: OnFinal | None = None,
    stop_when: Callable[[], bool] | None = None,
    on_call: OnCall | None = None,
) -> LoopOutcome:
    """Runs the model on `messages` (extended in place) until it answers, `stop_when` holds, or it has
    used `request.max_turns` model calls."""
    offered = {tool.name for tool in tools}
    outcome = LoopOutcome(status="max_turns")

    async def emit(type_: str, payload: dict[str, Any]) -> None:
        if on_event is not None:
            await on_event(type_, payload)

    while outcome.turns < request.max_turns:
        body = request.body(messages, tools)
        answer = await complete(body)
        outcome.turns += 1
        usage = answer.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        for key, value in usage.items():
            if isinstance(value, int):
                outcome.usage[key] = outcome.usage.get(key, 0) + value
        reported = usage.get("prompt_tokens")
        outcome.prompt_tokens.append(reported if isinstance(reported, int) else None)
        if on_call is not None:
            await on_call(body, usage)
        message = _message(answer)
        calls = [call for call in message.get("tool_calls") or [] if isinstance(call, dict)]
        content = message.get("content")
        text = content if isinstance(content, str) else ""
        await emit("prompt.turn", {"turn": outcome.turns, "tool_calls": len(calls), "text": text[:_EXCERPT]})
        messages.append(
            {"role": "assistant", "content": text or None, **({"tool_calls": calls} if calls else {})}
        )
        if not calls:
            follow_up = await on_final(text) if on_final is not None else None
            if follow_up is None:
                outcome.status, outcome.text = "final", text
                return outcome
            messages.append({"role": "user", "content": follow_up})
            continue
        for call in calls:
            messages.append(await _answer(call, offered, call_tool, outcome, emit))
        if stop_when is not None and stop_when():
            outcome.status = "stopped"
            return outcome
    return outcome


async def _answer(
    call: dict[str, Any],
    offered: set[str],
    call_tool: CallTool,
    outcome: LoopOutcome,
    emit: OnEvent,
) -> dict[str, Any]:
    """The tool message that answers one call: the tool's text, or why it was not called."""
    function = call.get("function") or {}
    name = str(function.get("name") or "")
    call_id = str(call.get("id") or "")
    if name not in offered:
        outcome.refused.append(name)
        await emit("prompt.tool_refused", {"tool": name[:200], "reason": "not offered"})
        return _tool_message(call_id, f"no tool named `{name}` is offered to you: use only the tools listed")
    try:
        arguments = json.loads(function.get("arguments") or "{}")
    except json.JSONDecodeError as exc:
        return _tool_message(call_id, f"the arguments are not valid JSON ({exc.msg}): call the tool again")
    if not isinstance(arguments, dict):
        return _tool_message(call_id, "the arguments must be a JSON object: call the tool again")
    outcome.tool_calls += 1
    text = await call_tool(name, arguments)
    await emit(
        "prompt.tool_call",
        {"tool": name, "arguments": json.dumps(arguments)[:_EXCERPT], "answer": text[:_EXCERPT]},
    )
    return _tool_message(call_id, text)


def _tool_message(call_id: str, text: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "content": text}


def _message(answer: dict[str, Any]) -> dict[str, Any]:
    choices = answer.get("choices") or []
    first = choices[0] if choices and isinstance(choices[0], dict) else {}
    message = first.get("message")
    return message if isinstance(message, dict) else {}
