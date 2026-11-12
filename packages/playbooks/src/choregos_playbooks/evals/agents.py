# SPDX-License-Identifier: Apache-2.0
"""Evals of catalogue AGENTS (ADR 0020, ADR 0052): what the agent is told, and what it answers.

A playbook eval checks a rendered prompt. An agent eval checks two things, in this order:

1. **instructions** — deterministic: the catalogue entry's instructions carry what the task needs
   (the output contract, the tools to read, the "never write" rule). Emptied or degraded
   instructions fail here, in CI, without a model.
2. **answer** — the agent's report, scored by MECHANISMS (ADR 0010): the facts the recorded
   evidence supports must be in it, what is forbidden must not, the sections must be there in
   order. The answer comes from a live model through the gateway (nightly) or from a file
   (`--answer`). Without either, the result is `not run` — never `passed` (ADR 0020, conditions).

The evidence is not invented for the eval: it is what the connectors return from their fakes —
the recorded Hubble trace, the Cilium policies — through the same code the broker runs. A case may
name the scene each connector's fake holds (`connectors: {pfsense: {kind: firewall, scene:
ssh-exposure}}`, `choregos_adapters.fakes.scenes`).

S57-09 (ADR 0122 §9): the eval matrix plays a case in two cells, `mcp` and `scripts` (`tools_cells`),
and judges what the run DID beside what it answered: `check_assertions` reads an observation (model
requests, tool calls by origin, actions proposed and executed, security events) against the case's
`assertions`. A case may carry its own agent (`agent_spec.instructions`) when no catalogue agent fits.

#1372: a plugin ships agent cases in this format (`contents.matrix_cases`); the matrix names one
`plugin:<plugin>/<case>` (`plugin_case`), loads it from the plugin as the offline plugins hold it, and plays
its connectors `plugin:<plugin>/<server>` on that server's fake.

S57-11b: a case judges the TASK, not the door. The first live matrix (2026-11-06) failed right answers on
a print's wording (`PROPOSED, NOT DONE` is the Python client's, the MCP door says `PROPOSED, NOT
EXECUTED`) and on how many calls came through which door. So:

- a fact of the answer (`answer_must_contain`) is a substring as before, or a mapping `{fact, tokens}`
  (every token on one line of the answer, a word token never inside a longer word, a number token
  matched by value, `24.3` by `24.30` or `24.31`) or `{fact, pattern, distinct?}` (a regular expression,
  at least `distinct` different matches); `answer_must_not_contain` reads the same forms;
- `calls_by_origin` and `max_model_requests` are MEASURES (`MEASURE_KINDS`): what the comparison of the
  two doors measures, written under `measures`, recorded beside the verdict (`expectations`), never a
  pass criterion — also when an older case (a plugin's) still writes them under `assertions`. Only
  guarantees (`GUARANTEE_KINDS`: actions proposed and executed, tools never called, the platform's
  security events) pass or fail a run.

The catalogue (`choregos_core`) and the connectors (`choregos_adapters`) are imported lazily: the
playbooks package does not depend on them, and an eval that cannot reach them says `not run`.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

import yaml

AGENT_CASES_DIR = Path(__file__).parent / "agent_cases"
#: The doors of a run's tools a case may be played through (ADR 0122): the model's MCP tools only, or
#: those and the tool bridge, from a script.
TOOLS_CELLS = ("mcp", "scripts")
#: An action that waits for a person: nothing was done (ADR 0035).
PROPOSED_STATUSES = frozenset({"pending_approval"})
#: An action whose write was played: running, waiting for its evidence, finished either way, or sent
#: and never answered (`in_doubt`, ADR 0149: nobody knows whether it was made, and it did leave).
EXECUTED_STATUSES = frozenset({"running", "awaiting_evidence", "succeeded", "failed", "in_doubt"})
#: What a case may check of what its run did (S57-09): its measures, then its guarantees.
ASSERTION_KINDS = (
    "max_model_requests",
    "calls_by_origin",
    "proposed_actions",
    "executed_actions",
    "no_tool_called",
    "security_event",
)
#: What the comparison of the doors measures (S57-11b): recorded, never a pass criterion — a run that did
#: its task through the other door, or in more model calls, did its task.
MEASURE_KINDS = frozenset({"max_model_requests", "calls_by_origin"})
#: What must hold whichever door the run used: what it proposed, what it executed, what it never called,
#: what the platform's guard saw.
GUARANTEE_KINDS = tuple(kind for kind in ASSERTION_KINDS if kind not in MEASURE_KINDS)
#: A number in an answer, not part of a word: `wan#17` holds 17, `x42` none, `24.3 %` holds 24.3.
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?!\w)")
_NUMERIC = re.compile(r"\d+(?:\.\d+)?")


@dataclass
class AgentCaseResult:
    case: str
    agent: str
    instructions_passed: bool
    instructions_missing: list[str] = field(default_factory=list)
    #: `passed`, `failed` or `not run`
    answer: str = "not run"
    answer_score: float | None = None
    answer_missing: list[str] = field(default_factory=list)
    answer_forbidden: list[str] = field(default_factory=list)
    sections_out_of_order: bool = False
    reason: str = ""

    @property
    def passed(self) -> bool:
        """What blocks: the instructions always; the answer when it was run."""
        return self.instructions_passed and self.answer != "failed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "case": self.case,
            "agent": self.agent,
            "passed": self.passed,
            "instructions_passed": self.instructions_passed,
            "instructions_missing": self.instructions_missing,
            "answer": self.answer,
            "answer_score": None if self.answer_score is None else round(self.answer_score, 3),
            "answer_missing": self.answer_missing,
            "answer_forbidden": self.answer_forbidden,
            "sections_out_of_order": self.sections_out_of_order,
            "reason": self.reason,
        }


def load_case(path: Path) -> dict[str, Any]:
    case: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    case.setdefault("name", path.stem)
    return case


#: How the matrix names a plugin's case (#1372).
PLUGIN_CASE = "plugin:"


def plugin_case(ref: str, catalogue: Any = None) -> dict[str, Any] | None:
    """The matrix case `plugin:<plugin>/<case>` a plugin ships (`contents.matrix_cases`, #1372), from the
    offline plugins (`choregos_core.agent_catalogue`), or from `catalogue` when given (an organisation's
    view); `None` when no plugin read ships it, or the catalogue is unreachable. Its `name` is the file's
    stem and `plugin` the plugin that ships it."""
    if catalogue is None:
        try:
            from choregos_core.agent_catalogue import catalogue as offline
        except ImportError:
            return None
        catalogue = offline()
    found = catalogue.matrix_case(ref)
    if found is None:
        return None
    return {**json.loads(json.dumps(found.case)), "name": found.name, "plugin": found.plugin}


def case_instructions(case: dict[str, Any]) -> str | None:
    """The instructions the case's agent runs with: its own (`agent_spec.instructions`, a case-local
    agent, S57-09), else the catalogue's for its slug."""
    own = (case.get("agent_spec") or {}).get("instructions")
    return str(own) if own is not None else catalogue_instructions(str(case["agent"]))


def catalogue_instructions(slug: str) -> str | None:
    """The instructions the catalogue (the offline plugins, ADR 0093 §6) ships for `slug` — `None` when
    the catalogue is unreachable or no plugin ships it."""
    try:
        from choregos_core.agent_catalogue import entry
    except ImportError:
        return None
    found = entry(slug)
    return None if found is None else str(found.document["spec"].get("instructions", ""))


def score_instructions(case: dict[str, Any], instructions: str) -> list[str]:
    lowered = instructions.lower()
    return [needle for needle in case.get("instructions_must_contain", []) if needle.lower() not in lowered]


def report_of(answer: str, output: str | None) -> str:
    """The report inside the agent's output (`{"<output>": "…"}`), or the answer as written."""
    if output:
        try:
            parsed = json.loads(answer)
        except json.JSONDecodeError:
            return answer
        if isinstance(parsed, dict) and isinstance(parsed.get(output), str):
            return str(parsed[output])
    return answer


def fact_label(fact: str | dict[str, Any]) -> str:
    """What a reason calls a fact: the text itself, or a mapping's `fact`."""
    return (
        fact if isinstance(fact, str) else str(fact.get("fact") or fact.get("tokens") or fact.get("pattern"))
    )


def _on_line(line: str, token: str) -> bool:
    """Whether `token` is on `line`: a number by value (`24.3` holds for `24.30` and `24.31`, `42` for
    `42` only), any other text case-insensitively and never inside a longer word (`wan#3` is not in
    `wan#30`, `PARTNERS` not in `PARTNERS_CLOUD`)."""
    if _NUMERIC.fullmatch(token):
        places = len(token.partition(".")[2])
        target = Decimal(token)
        for found in _NUMBER.findall(line):
            given = len(found.partition(".")[2])
            if places == 0 and given == 0 and Decimal(found) == target:
                return True
            if places and given >= places:
                rounded = Decimal(found).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
                if rounded == target:
                    return True
        return False
    pattern = re.escape(token)
    if re.match(r"\w", token):
        pattern = rf"(?<!\w){pattern}"
    if re.search(r"\w$", token):
        pattern = rf"{pattern}(?!\w)"
    return re.search(pattern, line, re.IGNORECASE) is not None


def fact_holds(fact: str | dict[str, Any], report: str) -> bool:
    """Whether the report states `fact` (S57-11b): a substring, case-insensitive; `{tokens}`, every token on
    one line; `{pattern, distinct}`, a case-insensitive regular expression found `distinct` different ways
    (once by default)."""
    if isinstance(fact, str):
        return fact.lower() in report.lower()
    if "pattern" in fact:
        found = {match.group(0).lower() for match in re.finditer(str(fact["pattern"]), report, re.IGNORECASE)}
        return len(found) >= int(fact.get("distinct", 1))
    tokens = [str(token) for token in fact.get("tokens") or []]
    return bool(tokens) and any(
        all(_on_line(line, token) for token in tokens) for line in report.splitlines()
    )


def score_answer(case: dict[str, Any], answer: str) -> tuple[float, list[str], list[str], bool]:
    """(score, missing facts, forbidden present, sections out of order) — predicates, no judge."""
    report = report_of(answer, case.get("output"))
    lowered = report.lower()
    facts = list(case.get("answer_must_contain", []))
    missing = [fact_label(fact) for fact in facts if not fact_holds(fact, report)]
    forbidden = [
        fact_label(needle) for needle in case.get("answer_must_not_contain", []) if fact_holds(needle, report)
    ]
    positions = [lowered.find(section.lower()) for section in case.get("answer_sections", [])]
    out_of_order = any(p < 0 for p in positions) or positions != sorted(positions)
    score = (len(facts) - len(missing)) / (len(facts) or 1)
    if forbidden or out_of_order:
        score = 0.0
    return score, missing, forbidden, out_of_order


async def evidence(case: dict[str, Any]) -> dict[str, Any]:
    """What the agent's tools return on the fakes, for the case's connector calls — the broker's own
    code. A call to a platform tool (`web_fetch`, no `kind`) is the eval matrix's to play."""
    import choregos_adapters
    from choregos_adapters.fakes.scenes import scene

    scenes = {str(c["kind"]): str(c.get("scene", "default")) for c in (case.get("connectors") or {}).values()}
    results: dict[str, Any] = {}
    for call in case.get("evidence", []):
        if "kind" not in call:
            continue
        kind = str(call["kind"])
        # The kind's FAKE, whatever `CHOREGOS_FAKES` says: the evidence is the recording, by design.
        connector = (
            scene(kind, scenes[kind]).connector
            if kind in scenes
            else choregos_adapters.build(kind, "fake", {})
        )
        results[str(call["tool"])] = await connector.execute(
            str(call["operation"]), dict(call.get("arguments") or {})
        )
    return results


def _counted(assertion: dict[str, Any], found: int, what: str) -> str | None:
    """`count` (exact), `min` and `max` of an assertion against what was found; `None` when it holds."""
    if "count" in assertion and found != int(assertion["count"]):
        return f"{what}: {found}, expected {int(assertion['count'])}"
    if "min" in assertion and found < int(assertion["min"]):
        return f"{what}: {found}, expected at least {int(assertion['min'])}"
    if "max" in assertion and found > int(assertion["max"]):
        return f"{what}: {found}, expected at most {int(assertion['max'])}"
    return None


def _named(entry: dict[str, Any], assertion: dict[str, Any]) -> bool:
    """Whether a call or an action is the assertion's tool (a glob, `entra__*`); any without one."""
    patterns = assertion.get("tools") or ([assertion["tool"]] if assertion.get("tool") else ["*"])
    return any(fnmatchcase(str(entry.get("tool", "")), str(pattern)) for pattern in patterns)


def check_assertions(case: dict[str, Any], observation: dict[str, Any]) -> list[str]:
    """What the run did against the case's GUARANTEES (S57-09, S57-11b): one reason per guarantee of its
    `assertions` that does not hold, none when they all do. A measure kind written under `assertions`
    (`MEASURE_KINDS`, as an older or a plugin's case may) is a measure: `check_measures` reads it, never
    this. An assertion with a `cell` holds only in that tools cell and is not checked in the other.

    `observation` is what the matrix measured of one run: `tools` (its cell), `requests` (model calls,
    from the gateway; `None` when nothing measured them), `calls` (`{tool, origin, status_code}`, every
    call that reached the platform), `actions` (`{tool, status}`), `security_events` (`{type, tool}`).

    - `proposed_actions: {tool?, count | min | max}` — actions waiting for a person (`pending_approval`);
    - `executed_actions: {tool?, count | min | max}` — actions whose write was played (`running`,
      `awaiting_evidence`, `succeeded`, `failed`, `in_doubt`);
    - `no_tool_called: {tool | tools}` — no call to these tools (globs), through either door;
    - `security_event: {type, count | min | max}` — the platform's security events of that type; at
      least one when no bound is given.
    """
    guarantees = [a for a in case.get("assertions") or [] if str(a.get("kind")) not in MEASURE_KINDS]
    return unmet(guarantees, observation)


def check_measures(case: dict[str, Any], observation: dict[str, Any]) -> list[str]:
    """What the run did against what the case EXPECTS of its doors (S57-11b): one note per expectation of
    its `measures` (and of a measure kind under `assertions`) that the run did not meet. A note, never a
    failure: these are the numbers the comparison of the doors measures.

    - `max_model_requests: {max}` — the run's model calls; an unmeasured run does not meet it;
    - `calls_by_origin: {origin, count | min | max}` — the calls that came through a door (`model`,
      `script`, `job`).
    """
    older = [a for a in case.get("assertions") or [] if str(a.get("kind")) in MEASURE_KINDS]
    return unmet([*(case.get("measures") or []), *older], observation)


def unmet(entries: list[dict[str, Any]], observation: dict[str, Any]) -> list[str]:
    """One reason per entry (an assertion or a measure) that does not hold on `observation`; an entry with
    a `cell` is read in that tools cell only."""
    cell = str(observation.get("tools", "mcp"))
    failed: list[str] = []
    for assertion in entries:
        kind = str(assertion.get("kind"))
        where = assertion.get("cell")
        if where is not None and where not in TOOLS_CELLS:
            failed.append(f"{kind}: unknown cell {where!r} (expected one of {', '.join(TOOLS_CELLS)})")
            continue
        if where is not None and where != cell:
            continue
        reason = _assertion(kind, assertion, observation)
        if reason is not None:
            failed.append(reason)
    return failed


def _assertion(kind: str, assertion: dict[str, Any], observation: dict[str, Any]) -> str | None:
    calls = list(observation.get("calls") or [])
    actions = list(observation.get("actions") or [])
    if kind == "max_model_requests":
        requests = observation.get("requests")
        if requests is None:
            return "max_model_requests: the run's model requests were not measured"
        limit = int(assertion["max"])
        return None if int(requests) <= limit else f"max_model_requests: {requests}, expected at most {limit}"
    if kind == "calls_by_origin":
        origin = str(assertion["origin"])
        found = sum(1 for call in calls if call.get("origin", "model") == origin)
        return _counted(assertion, found, f"calls_by_origin {origin}")
    if kind in {"proposed_actions", "executed_actions"}:
        statuses = PROPOSED_STATUSES if kind == "proposed_actions" else EXECUTED_STATUSES
        found = sum(1 for action in actions if action.get("status") in statuses and _named(action, assertion))
        return _counted(assertion, found, kind)
    if kind == "no_tool_called":
        called = sorted({str(call.get("tool")) for call in calls if _named(call, assertion)})
        return f"no_tool_called: called {', '.join(called)}" if called else None
    if kind == "security_event":
        kind_of_event = str(assertion["type"])
        found = sum(
            1 for event in observation.get("security_events") or [] if event.get("type") == kind_of_event
        )
        bounds = assertion if {"count", "min", "max"} & set(assertion) else {**assertion, "min": 1}
        return _counted(bounds, found, f"security_event {kind_of_event}")
    return f"unknown assertion: {kind} (expected one of {', '.join(ASSERTION_KINDS)})"


def judge(case: dict[str, Any], answer: str, observation: dict[str, Any]) -> dict[str, Any]:
    """A played case's verdict: its answer scored by predicates, what its run did checked by its
    guarantees. It passes when the score reaches `min_score` and every guarantee holds; the
    expectations its run did not meet (`check_measures`) are said beside it, and change nothing."""
    score, missing, forbidden, out_of_order = score_answer(case, answer)
    failed = check_assertions(case, observation)
    expectations = check_measures(case, observation)
    passed = score >= float(case.get("min_score", 1.0)) and not failed
    reasons = [
        *failed,
        *([f"missing from the answer: {', '.join(missing)}"] if missing else []),
        *([f"forbidden in the answer: {', '.join(forbidden)}"] if forbidden else []),
        *(["the answer's sections are missing or out of order"] if out_of_order else []),
    ]
    return {
        "ok": passed,
        "score": round(score, 3),
        "reason": ""
        if passed
        else "; ".join(reasons) or f"score {score:.2f} below {case.get('min_score', 1.0)}",
        "answer_missing": missing,
        "answer_forbidden": forbidden,
        "assertions_failed": failed,
        "expectations": expectations,
    }


def _ask_the_model(case: dict[str, Any], instructions: str, tools: dict[str, Any], model: str) -> str | None:
    """The live answer through the gateway; `None` when no gateway is configured."""
    import httpx

    base_url = os.environ.get("CHOREGOS_GATEWAY_URL", "")
    key = os.environ.get("CHOREGOS_GATEWAY_KEY", "")
    if not base_url or not key:
        return None
    from choregos_core.instructions import render_instructions

    system = render_instructions(instructions)
    user = (
        str(case.get("task", ""))
        + "\n\n"
        + "\n\n".join(
            f"Result of `{tool}`:\n```json\n{json.dumps(result, indent=1)}\n```"
            for tool, result in tools.items()
        )
    )
    response = httpx.post(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
        },
        timeout=120,
    )
    response.raise_for_status()
    return str(response.json()["choices"][0]["message"]["content"])


def evaluate_agent_case(
    path: Path,
    *,
    instructions: str | None = None,
    answer: str | None = None,
    live: bool = False,
    model: str = "platform/strong",
) -> AgentCaseResult:
    """`instructions`: an override (a test empties them); `answer`: a recorded answer to score;
    `live`: ask the model through the gateway when no answer is given."""
    import asyncio

    case = load_case(path)
    slug = str(case["agent"])
    text = instructions if instructions is not None else case_instructions(case)
    if text is None:
        return AgentCaseResult(
            case=case["name"],
            agent=slug,
            instructions_passed=False,
            reason="the agent catalogue is unreachable",
        )
    missing = score_instructions(case, text)
    result = AgentCaseResult(
        case=case["name"], agent=slug, instructions_passed=not missing, instructions_missing=missing
    )
    if answer is None and live:
        try:
            answer = _ask_the_model(case, text, asyncio.run(evidence(case)), model)
        except Exception as error:  # a model or gateway down is `not run`, never `passed`
            result.reason = f"live answer unavailable: {str(error)[:200]}"
            return result
        if answer is None:
            result.reason = "no gateway configured (CHOREGOS_GATEWAY_URL / CHOREGOS_GATEWAY_KEY)"
    if answer is None:
        return result
    score, absent, forbidden, out_of_order = score_answer(case, answer)
    result.answer_score, result.answer_missing = score, absent
    result.answer_forbidden, result.sections_out_of_order = forbidden, out_of_order
    result.answer = "passed" if score >= float(case.get("min_score", 1.0)) else "failed"
    return result


def agent_cases() -> list[Path]:
    return sorted(AGENT_CASES_DIR.glob("*.yaml"))
