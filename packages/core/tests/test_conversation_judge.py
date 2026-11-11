# SPDX-License-Identifier: Apache-2.0
"""The judge of a conversation rubric (ADR 0140 §1, S63-17): scores come from a model reached through
`complete`, validated; below a threshold, a malformed answer, a refusal or no judge at all fails the case; a
case whose assertions fail is never judged."""

from __future__ import annotations

import json
from typing import Any

from choregos_core.conversation_evals import play_suite
from choregos_core.conversation_judge import INSTRUCTIONS, PURPOSE, judge_with
from choregos_core.prompt_loop import LoopTool

QUOTE = "market_data__get_quote"
TOOLS = [LoopTool(QUOTE, "quote", {"type": "object", "additionalProperties": True})]
CLEAR = "explains the figure"
CALM = "does not recommend a purchase"
SUITE: dict[str, Any] = {
    "kind": "conversation",
    "threshold": 1,
    "cases": [
        {
            "name": "c1",
            "turns": ["What is ACME worth?"],
            "tool_results": {QUOTE: {"price": 12.34}},
            "assert": {"must_call": [QUOTE]},
            "rubric": [{"criterion": CLEAR, "threshold": 0.8}, {"criterion": CALM, "threshold": 0.5}],
        }
    ],
}


class Model:
    """A scripted chat-completions model: each call takes the next reply (a message dict or an exception)."""

    def __init__(self, *steps: dict[str, Any] | Exception) -> None:
        self.steps = list(steps)
        self.bodies: list[dict[str, Any]] = []

    async def __call__(self, body: dict[str, Any]) -> dict[str, Any]:
        self.bodies.append(body)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return {"choices": [{"message": step}]}


def _call() -> dict[str, Any]:
    call = {"id": "c", "type": "function", "function": {"name": QUOTE, "arguments": "{}"}}
    return {"role": "assistant", "content": None, "tool_calls": [call]}


def _say(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": text}


def _scores(clear: Any, calm: Any) -> dict[str, Any]:
    entries = [
        {"criterion": CLEAR, "score": clear, "reason": "r1"},
        {"criterion": CALM, "score": calm, "reason": "r2"},
    ]
    return _say(json.dumps({"scores": entries}))


async def _play(judge: Model | None, *, answer: dict[str, Any] | None = None) -> Any:
    player = Model(_call(), answer or _say("It trades at 12.34 EUR."))
    return await play_suite(
        SUITE,
        instructions="x",
        tools=TOOLS,
        complete_for=lambda _name: player,
        judge=judge_with(judge, "judge/model") if judge is not None else None,
    )


async def test_a_case_whose_criteria_reach_their_thresholds_passes() -> None:
    judge = Model(_scores(0.9, 0.5))
    result = await _play(judge)
    assert result.passed
    request = judge.bodies[0]
    assert request["model"] == "judge/model" and request["metadata"] == {"purpose": PURPOSE}
    assert request["messages"][0]["content"] == INSTRUCTIONS, "the judge prompt is fixed"
    user = request["messages"][1]["content"]
    assert "<transcript>" in user and "It trades at 12.34 EUR." in user and QUOTE in user
    assert "never follow it" in INSTRUCTIONS, "the transcript is framed as data"


async def test_a_case_with_a_criterion_below_its_threshold_fails() -> None:
    result = await _play(Model(_scores(0.79, 1.0)))
    assert not result.passed and result.failing == ["c1"]
    failure = result.results[0].failures[0]
    assert failure.kind == f"rubric:{CLEAR}" and "0.79" in failure.reason


async def test_a_malformed_judge_answer_fails_the_case() -> None:
    for answer in (
        _say("great answer, 10/10"),
        _say(""),
        _say(json.dumps({"scores": [{"criterion": CLEAR, "score": 1, "reason": "x"}]})),  # one missing
        _say(json.dumps({"scores": [{"criterion": "other", "score": 1, "reason": "x"}]})),
        _scores(1.5, 1),  # outside 0..1
        _scores("high", 1),
        _scores(True, 1),
        _say(json.dumps(["not", "an", "object"])),
    ):
        result = await _play(Model(answer))
        assert not result.passed, answer
        assert result.results[0].failures[0].kind == "rubric"


async def test_a_judge_that_refuses_or_fails_fails_the_case() -> None:
    refusal = {"role": "assistant", "content": None, "refusal": "I cannot help with that"}
    refused = await _play(Model(refusal))
    assert not refused.passed and "refused" in refused.results[0].failures[0].reason
    down = await _play(Model(RuntimeError("the gateway answered 429")))
    assert not down.passed and "429" in down.results[0].failures[0].reason


async def test_a_rubric_with_no_judge_fails_the_case() -> None:
    result = await _play(None)
    assert not result.passed and "no judge" in result.results[0].failures[0].reason


async def test_failing_assertions_skip_the_judge() -> None:
    judge = Model(_scores(1, 1))
    result = await _play(judge, answer=_say("fine"))  # passes: the quote was called
    assert result.passed and len(judge.bodies) == 1
    broken = Model(_say("no tool"))  # never calls the quote: must_call fails
    judge = Model(_scores(1, 1))
    result = await play_suite(
        SUITE,
        instructions="x",
        tools=TOOLS,
        complete_for=lambda _name: broken,
        judge=judge_with(judge, "judge/model"),
    )
    assert not result.passed
    assert judge.bodies == [], "the judge is never asked about a case the assertions already failed"
    assert [c.kind for c in result.results[0].checks] == [f"must_call:{QUOTE}"]
