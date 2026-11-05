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

The catalogue (`choregos_core`) and the connectors (`choregos_adapters`) are imported lazily: the
playbooks package does not depend on them, and an eval that cannot reach them says `not run`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
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
#: An action whose write was played: running, waiting for its evidence, or finished either way.
EXECUTED_STATUSES = frozenset({"running", "awaiting_evidence", "succeeded", "failed"})
#: What a case may assert about what its run did (S57-09).
ASSERTION_KINDS = (
    "max_model_requests",
    "calls_by_origin",
    "proposed_actions",
    "executed_actions",
    "no_tool_called",
    "security_event",
)


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


def case_instructions(case: dict[str, Any]) -> str | None:
    """The instructions the case's agent runs with: its own (`agent_spec.instructions`, a case-local
    agent, S57-09), else the catalogue's for its slug."""
    own = (case.get("agent_spec") or {}).get("instructions")
    return str(own) if own is not None else catalogue_instructions(str(case["agent"]))


def catalogue_instructions(slug: str) -> str | None:
    """The instructions the catalogue (the offline plugins, ADR 0093 §6) ships for `slug` — `None` when
    the catalogue is unreachable or no plugin ships it."""
    try:
        from choregos_core.catalogue_d_agents import entree
    except ImportError:
        return None
    found = entree(slug)
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


def score_answer(case: dict[str, Any], answer: str) -> tuple[float, list[str], list[str], bool]:
    """(score, missing facts, forbidden present, sections out of order) — predicates, no judge."""
    report = report_of(answer, case.get("output"))
    lowered = report.lower()
    facts = list(case.get("answer_must_contain", []))
    missing = [fact for fact in facts if fact.lower() not in lowered]
    forbidden = [needle for needle in case.get("answer_must_not_contain", []) if needle.lower() in lowered]
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
    """What the run did against the case's `assertions` (S57-09): one reason per assertion that does
    not hold, none when they all do. An assertion with a `cell` holds only in that tools cell and is
    not checked in the other.

    `observation` is what the matrix measured of one run: `tools` (its cell), `requests` (model calls,
    from the gateway; `None` when nothing measured them), `calls` (`{tool, origin, status_code}`, every
    call that reached the platform), `actions` (`{tool, status}`), `security_events` (`{type, tool}`).

    - `max_model_requests: {max}` — the run's model calls; an unmeasured run fails it;
    - `calls_by_origin: {origin, count | min | max}` — the calls that came through a door (`model`,
      `script`, `job`);
    - `proposed_actions: {tool?, count | min | max}` — actions waiting for a person (`pending_approval`);
    - `executed_actions: {tool?, count | min | max}` — actions whose write was played (`running`,
      `awaiting_evidence`, `succeeded`, `failed`);
    - `no_tool_called: {tool | tools}` — no call to these tools (globs), through either door;
    - `security_event: {type, count | min | max}` — the platform's security events of that type; at
      least one when no bound is given.
    """
    cell = str(observation.get("tools", "mcp"))
    failed: list[str] = []
    for assertion in case.get("assertions") or []:
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
    assertions. It passes when the score reaches `min_score` and every assertion holds."""
    score, missing, forbidden, out_of_order = score_answer(case, answer)
    failed = check_assertions(case, observation)
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
