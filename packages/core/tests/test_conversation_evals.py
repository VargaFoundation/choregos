# SPDX-License-Identifier: Apache-2.0
"""Deterministic conversation evals (ADR 0140, S63-17): each assertion kind has a passing and a failing
case, a case is played end to end through the prompt loop on a scripted model, and the loader holds an
assistant's suite to the format and to the assistant's tools."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from choregos_core.conversation_evals import (
    Journal,
    ToolCall,
    evaluate_case,
    evaluate_suite,
    loop_tools,
    play_case,
    play_suite,
)
from choregos_core.conversation_judge import judge_with
from choregos_core.plugins import PluginError, load_directory

FIXTURE = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "plugins" / "investment-assistant"
SUITE = "evals/investment-assistant.yaml"
QUOTE, ORDER, POSITIONS = "market_data__get_quote", "orders__place_order", "brokerage__list_positions"
CITED = "It trades at 12.34 EUR (source: market_data, 2026-11-10)."


def _journal(calls: list[str], *texts: str) -> Journal:
    return Journal(calls=[ToolCall(name) for name in calls], texts=list(texts))


def _verdict(rules: dict[str, Any], journal: Journal, context: dict[str, Any] | None = None) -> bool:
    case = {"name": "c", "turns": ["hi"], "assert": rules, "context": context or {}}
    return evaluate_case(case, journal).passed


# -- must_call ----------------------------------------------------------------------------------------------


def test_must_call_passes_when_called_and_fails_when_not() -> None:
    assert _verdict({"must_call": [QUOTE]}, _journal([QUOTE], "x"))
    assert not _verdict({"must_call": [QUOTE]}, _journal([POSITIONS], "x"))


def test_must_call_before_any_requires_the_first_call() -> None:
    rules = {"must_call": [{"tool": QUOTE, "before": "any"}]}
    assert _verdict(rules, _journal([QUOTE, POSITIONS], "x"))
    failed = evaluate_case({"name": "c", "turns": ["t"], "assert": rules}, _journal([POSITIONS, QUOTE], "x"))
    assert not failed.passed and "first call" in failed.failures[0].reason


def test_must_call_before_a_tool_and_with_arguments() -> None:
    rules = {"must_call": [{"tool": QUOTE, "before": POSITIONS}]}
    assert _verdict(rules, _journal([QUOTE, POSITIONS], "x"))
    assert not _verdict(rules, _journal([POSITIONS, QUOTE], "x"))
    with_args = {"must_call": [{"tool": QUOTE, "arguments_contain": {"isin": "FR1"}}]}
    good = Journal(calls=[ToolCall(QUOTE, {"isin": "FR1", "x": 1})], texts=["x"])
    bad = Journal(calls=[ToolCall(QUOTE, {"isin": "FR2"})], texts=["x"])
    assert _verdict(with_args, good) and not _verdict(with_args, bad)


def test_call_order_is_a_subsequence() -> None:
    rules = {"call_order": [[POSITIONS, QUOTE]]}
    assert _verdict(rules, _journal([POSITIONS, "news__search_news", QUOTE], "x"))
    assert not _verdict(rules, _journal([QUOTE, POSITIONS], "x"))
    assert not _verdict(rules, _journal([POSITIONS], "x"))


# -- must_not_call ------------------------------------------------------------------------------------------


def test_must_not_call_fails_only_when_the_tool_was_called() -> None:
    assert _verdict({"must_not_call": [ORDER]}, _journal([QUOTE], "x"))
    assert not _verdict({"must_not_call": [ORDER]}, _journal([QUOTE, ORDER], "x"))


def test_must_not_call_with_a_context_condition_binds_only_when_the_relation_holds() -> None:
    rules = {"must_not_call": [{"tool": POSITIONS, "when": {"context": {"relation": "coachee"}}}]}
    called = _journal([POSITIONS], "x")
    assert not _verdict(rules, called, {"relation": "coachee"})
    assert _verdict(rules, called, {"relation": "self"}), "the rule does not apply to another relation"


def test_must_not_call_when_another_tool_was_called() -> None:
    rules = {"must_not_call": [{"tool": ORDER, "when": {"called": POSITIONS}}]}
    assert not _verdict(rules, _journal([POSITIONS, ORDER], "x"))
    assert _verdict(rules, _journal([ORDER], "x")), "no positions were read: the rule does not apply"


# -- must_cite ----------------------------------------------------------------------------------------------

CITE = {"must_cite": [{"figure": r"\d+(?:\.\d+)? ?EUR", "source": r"\(source: [a-z_]+, \d{4}-\d{2}-\d{2}\)"}]}


def test_must_cite_passes_when_every_figure_is_followed_by_a_source_and_a_date() -> None:
    assert _verdict(CITE, _journal([], CITED))
    assert _verdict(CITE, _journal([], "No figure here."))


def test_must_cite_fails_on_a_bare_figure_or_a_source_without_a_date() -> None:
    assert not _verdict(CITE, _journal([], "It trades at 12.34 EUR."))
    assert not _verdict(CITE, _journal([], "It trades at 12.34 EUR (source: market_data)."))
    two = f"{CITED} It was 11.00 EUR last week."
    result = evaluate_case({"name": "c", "turns": ["t"], "assert": CITE}, _journal([], two))
    assert not result.passed and "11.00 EUR" in result.failures[0].reason


# -- must_not_contain, length -------------------------------------------------------------------------------


def test_must_not_contain_matches_whole_words_and_regexes() -> None:
    rules = {"must_not_contain": ["buy", {"regex": r"guarant\w+"}]}
    assert _verdict(rules, _journal([], "Nothing to see, a buyer's market."))
    assert not _verdict(rules, _journal([], "You could BUY now."))
    assert not _verdict(rules, _journal([], "A guaranteed gain."))


def test_length_bounds_the_words_of_each_answer() -> None:
    rules = {"length": {"min_words": 3, "max_words": 5}}
    assert _verdict(rules, _journal([], "one two three four"))
    assert not _verdict(rules, _journal([], "one two"))
    assert not _verdict(rules, _journal([], "ok fine", "one two three four five six"))


def test_an_invalid_regex_is_a_failed_check_not_a_crash() -> None:
    assert not _verdict({"must_not_contain": [{"regex": "("}]}, _journal([], "x"))
    assert not _verdict({"must_cite": [{"figure": "(", "source": "x"}]}, _journal([], "x"))


# -- the suite's threshold ----------------------------------------------------------------------------------


def test_a_suite_passes_at_its_threshold_and_fails_below_it() -> None:
    suite: dict[str, Any] = {
        "threshold": 0.5,
        "cases": [
            {"name": "a", "turns": ["t"], "assert": {"must_call": [QUOTE]}},
            {"name": "b", "turns": ["t"], "assert": {"must_call": [QUOTE]}},
        ],
    }
    journals = {"a": _journal([QUOTE], "x"), "b": _journal([], "x")}
    half = evaluate_suite(suite, journals)
    assert half.share == 0.5 and half.passed and half.failing == ["b"]
    assert not evaluate_suite({**suite, "threshold": 1.0}, journals).passed
    assert not evaluate_suite(suite, {"a": journals["a"]}).results[1].passed, "an unplayed case fails"


# -- a case played end to end -------------------------------------------------------------------------------


def _model(*steps: dict[str, Any]) -> Callable[[dict[str, Any]], Any]:
    script = list(steps)
    bodies: list[dict[str, Any]] = []

    async def complete(body: dict[str, Any]) -> dict[str, Any]:
        bodies.append(body)
        return {"choices": [{"message": script.pop(0)}]}

    complete.bodies = bodies  # type: ignore[attr-defined]
    return complete


def _call(name: str, **arguments: Any) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": f"call-{name}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }


def _say(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": text}


def _fixture_suite() -> dict[str, Any]:
    loaded = yaml.safe_load((FIXTURE / SUITE).read_text())
    assert isinstance(loaded, dict)
    return loaded


TOOLS = loop_tools([QUOTE, POSITIONS, ORDER, "news__search_news"])


async def test_a_scripted_model_plays_the_fixture_case_and_every_assertion_passes() -> None:
    case = _fixture_suite()["cases"][0]
    complete = _model(_call(QUOTE, isin="FR0000000001"), _say(CITED))
    journal = await play_case(case, instructions="be brief", tools=TOOLS, complete=complete)
    assert journal.names() == [QUOTE] and journal.calls[0].arguments == {"isin": "FR0000000001"}
    assert journal.texts == [CITED]
    sent = complete.bodies[-1]["messages"]  # type: ignore[attr-defined]
    assert sent[-2]["role"] == "tool" and json.loads(sent[-2]["content"])["price"] == 12.34, "scripted result"
    assert evaluate_case(case, journal).passed


async def test_the_same_case_fails_when_the_model_breaks_a_rule() -> None:
    case = _fixture_suite()["cases"][0]
    complete = _model(
        _call("news__search_news", q="acme"),
        _call(QUOTE),
        _call(ORDER),
        _say("Buy it, 12.34 EUR. " + "word " * 130),
    )
    journal = await play_case(case, instructions="x", tools=TOOLS, complete=complete)
    reasons = " | ".join(c.kind for c in evaluate_case(case, journal).failures)
    assert "must_call:market_data__get_quote" in reasons  # not first
    assert "must_not_call:orders__place_order" in reasons
    assert "must_cite" in reasons and "length" in reasons


async def test_scripted_answers_succeed_in_order_and_the_last_repeats() -> None:
    case = {
        "name": "seq",
        "turns": ["one", "two"],
        "tool_results": {QUOTE: [{"price": 1}, "second"]},
    }
    complete = _model(_call(QUOTE), _say("a"), _call(QUOTE), _call(QUOTE), _say("b"))
    journal = await play_case(case, instructions="x", tools=TOOLS, complete=complete)
    answers = [m["content"] for m in complete.bodies[-1]["messages"] if m["role"] == "tool"]  # type: ignore[attr-defined]
    assert answers == ['{"price": 1}', "second", "second"]
    assert journal.texts == ["a", "b"], "one final text per turn"


async def test_a_suite_played_gives_its_share() -> None:
    suite = _fixture_suite()
    suite["cases"] = suite["cases"][:1]
    criterion = suite["cases"][0]["rubric"][0]["criterion"]
    verdict = json.dumps({"scores": [{"criterion": criterion, "score": 0.9, "reason": "ok"}]})
    judge = judge_with(_model(_say(verdict)), "judge")
    good = _model(_call(QUOTE), _say(CITED))
    result = await play_suite(
        suite, instructions="x", tools=TOOLS, complete_for=lambda _name: good, judge=judge
    )
    assert result.passed and result.share == 1.0
    bad = _model(_call(QUOTE), _say("12.34 EUR."))
    result = await play_suite(
        suite, instructions="x", tools=TOOLS, complete_for=lambda _name: bad, judge=judge
    )
    assert not result.passed and result.failing == ["quote-is-read-first-and-cited"]


# -- the loader ---------------------------------------------------------------------------------


@pytest.fixture
def plugin(tmp_path: Path) -> Path:
    copy = tmp_path / "investment-assistant"
    shutil.copytree(FIXTURE, copy)
    return copy


def _edit_suite(root: Path, change: Callable[[dict[str, Any]], None]) -> list[str]:
    path = root / SUITE
    doc = yaml.safe_load(path.read_text())
    change(doc)
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    with pytest.raises(PluginError) as caught:
        load_directory(root)
    return caught.value.problems


def test_the_fixture_suite_is_valid_and_ships() -> None:
    assert SUITE in load_directory(FIXTURE).files


def test_a_suite_outside_the_format_is_refused_with_its_path(plugin: Path) -> None:
    problems = _edit_suite(plugin, lambda d: d["cases"][0]["assert"].update(length={"min_words": "many"}))
    assert any(p.startswith(f"{SUITE}:cases/0/assert/length/min_words") for p in problems), problems


def test_a_tool_that_is_not_an_operation_of_the_assistant_is_refused(plugin: Path) -> None:
    def change(doc: dict[str, Any]) -> None:
        doc["cases"][0]["assert"]["must_call"] = ["market_data__delete_everything"]
        doc["cases"][0]["tool_results"]["crm__get_contact"] = "x"

    problems = _edit_suite(plugin, change)
    assert any("cases/0/assert/must_call/0/tool: `market_data__delete_everything`" in p for p in problems)
    assert any("cases/0/tool_results/crm__get_contact: `crm__get_contact`" in p for p in problems)


def test_a_suite_for_another_agent_and_a_duplicate_case_are_refused(plugin: Path) -> None:
    def change(doc: dict[str, Any]) -> None:
        doc["agent"] = "someone-else"
        doc["cases"].append(dict(doc["cases"][0]))

    problems = _edit_suite(plugin, change)
    assert any(p.startswith(f"{SUITE}:agent: ") for p in problems)
    assert any("case `quote-is-read-first-and-cited` is declared twice" in p for p in problems)
