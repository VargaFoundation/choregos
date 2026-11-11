# SPDX-License-Identifier: Apache-2.0
"""Deterministic conversation evals (ADR 0140, `conversation-evals.schema.json`).

A conversation is judged on its journal: the ordered tool calls (name and arguments) and the final text of
each turn. The assertions are exact, never a model's opinion:

- `must_call`: a tool is called, optionally `before: any` (it is the first call of the conversation, before
  any other read) or before another tool, optionally with given arguments; `call_order`: tools are called in
  a given order;
- `must_not_call`: a tool is never called, optionally only `when` the scenario's `context` holds, or when
  another tool was called;
- `must_cite`: every figure a final text states is followed by a source (a pattern) within a few characters;
- `must_not_contain`: words (whole words, case-insensitive) or regular expressions no final text holds;
- `length`: a range of words for each final text.

`evaluate_case` and `evaluate_suite` are pure. `play_case` plays a case through the prompt loop (ADR 0080)
with a scripted model and the scripted tool results of the case, so that a suite is proved without a
network; the caller hands the model (`complete`), as a session does.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .prompt_loop import Complete, LoopRequest, LoopTool, run_loop

#: How far after a figure its source may end, unless the assertion says otherwise.
DEFAULT_CITE_WINDOW = 120
#: Scores a case's rubric on its journal (`conversation_judge.judge_with`): one check per criterion.
Judge = Callable[[Mapping[str, Any], "Journal"], Awaitable[list["Check"]]]
#: A tool a scripted source has no answer for.
NO_SCRIPTED_RESULT = "error: the source has no scripted result for this call"


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class Journal:
    """What a conversation did: the tool calls in order, and the final text of each turn."""

    calls: list[ToolCall] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)

    def names(self) -> list[str]:
        return [call.name for call in self.calls]


@dataclass(frozen=True)
class Check:
    """One assertion's verdict: `kind` is the assertion's key, `reason` says why it failed (or holds)."""

    kind: str
    passed: bool
    reason: str


@dataclass
class CaseResult:
    name: str
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if not check.passed]


@dataclass
class SuiteResult:
    """The share of cases that passed every assertion, against the suite's threshold."""

    results: list[CaseResult]
    threshold: float = 1.0

    @property
    def share(self) -> float:
        return sum(1 for r in self.results if r.passed) / len(self.results) if self.results else 1.0

    @property
    def passed(self) -> bool:
        return self.share >= self.threshold

    @property
    def failing(self) -> list[str]:
        return [r.name for r in self.results if not r.passed]


# ───────────────────────────── the assertions ─────────────────────────────


def _spec(item: str | Mapping[str, Any]) -> Mapping[str, Any]:
    return {"tool": item} if isinstance(item, str) else item


def evaluate_case(case: Mapping[str, Any], journal: Journal) -> CaseResult:
    """Each assertion of the case against the journal."""
    rules: Mapping[str, Any] = case.get("assert") or {}
    result = CaseResult(name=str(case["name"]))
    context: Mapping[str, Any] = case.get("context") or {}
    for item in rules.get("must_call") or []:
        spec = _spec(item)
        result.checks.append(_must_call(spec, journal))
    for order in rules.get("call_order") or []:
        result.checks.append(_call_order(order, journal))
    for item in rules.get("must_not_call") or []:
        spec = _spec(item)
        result.checks.append(_must_not_call(spec, context, journal))
    for item in rules.get("must_cite") or []:
        result.checks.append(_must_cite(item, journal))
    for item in rules.get("must_not_contain") or []:
        result.checks.append(_must_not_contain(item, journal))
    if rules.get("length"):
        result.checks.append(_length(rules["length"], journal))
    return result


def evaluate_suite(suite: Mapping[str, Any], journals: Mapping[str, Journal]) -> SuiteResult:
    """Every case of the suite against the journal of the same name; a case with no journal fails."""
    results: list[CaseResult] = []
    for case in suite.get("cases") or []:
        journal = journals.get(str(case["name"]))
        if journal is None:
            missing = CaseResult(name=str(case["name"]))
            missing.checks.append(Check("journal", False, "the case was not played"))
            results.append(missing)
        else:
            results.append(evaluate_case(case, journal))
    return SuiteResult(results=results, threshold=float(suite.get("threshold", 1.0)))


def _must_call(spec: Mapping[str, Any], journal: Journal) -> Check:
    tool = str(spec["tool"])
    kind = f"must_call:{tool}"
    names = journal.names()
    if tool not in names:
        return Check(kind, False, f"`{tool}` was never called")
    wanted: Mapping[str, Any] = spec.get("arguments_contain") or {}
    if wanted and not any(
        call.name == tool and all(call.arguments.get(k) == v for k, v in wanted.items())
        for call in journal.calls
    ):
        return Check(kind, False, f"`{tool}` was never called with arguments {json.dumps(wanted)}")
    before = spec.get("before")
    if before == "any" and names[0] != tool:
        return Check(kind, False, f"`{names[0]}` was called before `{tool}`; it had to be the first call")
    if isinstance(before, str) and before in names and names.index(before) < names.index(tool):
        return Check(kind, False, f"`{before}` was called before `{tool}`")
    return Check(kind, True, f"`{tool}` was called")


def _call_order(order: Sequence[str], journal: Journal) -> Check:
    kind = "call_order:" + ">".join(order)
    names = journal.names()
    position = -1
    for tool in order:
        try:
            position = names.index(tool, position + 1)
        except ValueError:
            return Check(kind, False, f"`{tool}` was not called after the previous tool of the order")
    return Check(kind, True, "the calls came in order")


def _must_not_call(spec: Mapping[str, Any], context: Mapping[str, Any], journal: Journal) -> Check:
    tool = str(spec["tool"])
    kind = f"must_not_call:{tool}"
    when: Mapping[str, Any] = spec.get("when") or {}
    for key, value in (when.get("context") or {}).items():
        if context.get(key) != value:
            return Check(kind, True, f"the rule does not apply: `{key}` is not {json.dumps(value)}")
    called = when.get("called")
    if called is not None and called not in journal.names():
        return Check(kind, True, f"the rule does not apply: `{called}` was not called")
    if tool in journal.names():
        return Check(kind, False, f"`{tool}` was called")
    return Check(kind, True, f"`{tool}` was never called")


def _must_cite(spec: Mapping[str, Any], journal: Journal) -> Check:
    kind = "must_cite"
    window = int(spec.get("within_chars", DEFAULT_CITE_WINDOW))
    try:
        figure, source = re.compile(spec["figure"]), re.compile(spec["source"])
    except re.error as error:
        return Check(kind, False, f"the pattern is not a regular expression: {error}")
    uncited: list[str] = []
    for text in journal.texts:
        for found in figure.finditer(text):
            tail = text[found.end() : found.end() + window]
            if not source.match(tail[_leading(tail) :]):
                uncited.append(found.group(0))
    if uncited:
        return Check(kind, False, "figures with no source after them: " + ", ".join(repr(u) for u in uncited))
    return Check(kind, True, "every figure is followed by its source")


def _leading(text: str) -> int:
    matched = re.match(r"[\s,;:.]*", text)
    return matched.end() if matched else 0


def _must_not_contain(item: str | Mapping[str, Any], journal: Journal) -> Check:
    if isinstance(item, str):
        label, pattern = item, rf"(?<!\w){re.escape(item)}(?!\w)"
    else:
        label, pattern = f"/{item['regex']}/", str(item["regex"])
    kind = f"must_not_contain:{label}"
    try:
        compiled = re.compile(pattern, re.IGNORECASE if isinstance(item, str) else 0)
    except re.error as error:
        return Check(kind, False, f"not a regular expression: {error}")
    for text in journal.texts:
        if compiled.search(text):
            return Check(kind, False, f"a final text contains {label}")
    return Check(kind, True, f"no final text contains {label}")


def _length(spec: Mapping[str, Any], journal: Journal) -> Check:
    low, high = spec.get("min_words"), spec.get("max_words")
    for index, text in enumerate(journal.texts, start=1):
        words = len(text.split())
        if low is not None and words < int(low):
            return Check("length", False, f"answer {index} has {words} words, at least {low} expected")
        if high is not None and words > int(high):
            return Check("length", False, f"answer {index} has {words} words, at most {high} expected")
    return Check("length", True, "every answer is within the range")


# ───────────────────────────── the names a suite uses ─────────────────────────────


def tools_named(case: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Every tool a case names, as `(json path inside the case, tool name)`."""
    found: list[tuple[str, str]] = []
    rules: Mapping[str, Any] = case.get("assert") or {}
    for index, item in enumerate(rules.get("must_call") or []):
        spec = _spec(item)
        found.append((f"assert/must_call/{index}/tool", str(spec["tool"])))
        before = spec.get("before")
        if isinstance(before, str) and before != "any":
            found.append((f"assert/must_call/{index}/before", before))
    for index, order in enumerate(rules.get("call_order") or []):
        found.extend((f"assert/call_order/{index}", str(tool)) for tool in order)
    for index, item in enumerate(rules.get("must_not_call") or []):
        spec = _spec(item)
        found.append((f"assert/must_not_call/{index}/tool", str(spec["tool"])))
        called = (spec.get("when") or {}).get("called")
        if called is not None:
            found.append((f"assert/must_not_call/{index}/when/called", str(called)))
    found.extend((f"tool_results/{name}", str(name)) for name in case.get("tool_results") or {})
    return found


# ───────────────────────────── playing a case ─────────────────────────────


def loop_tools(names: Iterable[str]) -> list[LoopTool]:
    """The tools offered to the model: each takes any arguments."""
    return [
        LoopTool(name, f"the operation `{name}`", {"type": "object", "additionalProperties": True})
        for name in names
    ]


class ScriptedSources:
    """The sources' fakes of one case: the scripted answer of each `<need>__<operation>`, in order."""

    def __init__(self, results: Mapping[str, Any]) -> None:
        self._results = dict(results)
        self._seen: dict[str, int] = {}
        self.journal = Journal()

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        self.journal.calls.append(ToolCall(name, dict(arguments)))
        if name not in self._results:
            return NO_SCRIPTED_RESULT
        answer = self._results[name]
        if isinstance(answer, list):
            index = self._seen.get(name, 0)
            self._seen[name] = index + 1
            answer = answer[min(index, len(answer) - 1)] if answer else ""
        return answer if isinstance(answer, str) else json.dumps(answer)


async def play_case(
    case: Mapping[str, Any],
    *,
    instructions: str,
    tools: Sequence[LoopTool],
    complete: Complete,
    model: str = "scripted",
    max_turns: int = 10,
) -> Journal:
    """The case's user turns through the prompt loop, the sources answering from `tool_results`."""
    sources = ScriptedSources(case.get("tool_results") or {})
    messages: list[dict[str, Any]] = [{"role": "system", "content": instructions}]
    for turn in case["turns"]:
        messages.append({"role": "user", "content": str(turn)})
        outcome = await run_loop(
            LoopRequest(model=model, max_turns=max_turns), messages, list(tools), complete, sources.call
        )
        sources.journal.texts.append(outcome.text)
    return sources.journal


async def play_suite(
    suite: Mapping[str, Any],
    *,
    instructions: str,
    tools: Sequence[LoopTool],
    complete_for: Callable[[str], Complete],
    judge: Judge | None = None,
    model: str = "scripted",
) -> SuiteResult:
    """Every case of a suite played, then evaluated; `complete_for(case name)` gives each case its model.

    The assertions come first and are exact. Only a case that passes them is judged on its `rubric`; a case
    that fails them is not (the judge is never asked about a case already lost). A rubric nobody can judge
    (`judge` is `None`) fails its case: nothing evaluated is never a pass."""
    journals: dict[str, Journal] = {}
    for case in suite.get("cases") or []:
        name = str(case["name"])
        journals[name] = await play_case(
            case, instructions=instructions, tools=tools, complete=complete_for(name), model=model
        )
    result = evaluate_suite(suite, journals)
    cases = {str(case["name"]): case for case in suite.get("cases") or []}
    for case_result in result.results:
        case = cases[case_result.name]
        if not case.get("rubric") or not case_result.passed:
            continue
        if judge is None:
            case_result.checks.append(Check("rubric", False, "the case has a rubric and no judge model"))
        else:
            case_result.checks.extend(await judge(case, journals[case_result.name]))
    return result
