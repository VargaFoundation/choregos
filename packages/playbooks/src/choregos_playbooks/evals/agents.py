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
the recorded Hubble trace, the Cilium policies — through the same code the broker runs.

The catalogue (`choregos_core`) and the connectors (`choregos_adapters`) are imported lazily: the
playbooks package does not depend on them, and an eval that cannot reach them says `not run`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

AGENT_CASES_DIR = Path(__file__).parent / "agent_cases"


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
    """What the agent's tools return on the fakes, for the case's calls — the broker's own code."""
    import choregos_adapters

    results: dict[str, Any] = {}
    for call in case.get("evidence", []):
        # The kind's FAKE, whatever `CHOREGOS_FAKES` says: the evidence is the recording, by design.
        connector = choregos_adapters.build(str(call["kind"]), "fake", {})
        results[str(call["tool"])] = await connector.executer(
            str(call["operation"]), dict(call.get("arguments") or {})
        )
    return results


def _ask_the_model(case: dict[str, Any], instructions: str, tools: dict[str, Any], model: str) -> str | None:
    """The live answer through the gateway; `None` when no gateway is configured."""
    import httpx

    base_url = os.environ.get("CHOREGOS_GATEWAY_URL", "")
    key = os.environ.get("CHOREGOS_GATEWAY_KEY", "")
    if not base_url or not key:
        return None
    from choregos_core.instructions import rendre_les_instructions

    system = rendre_les_instructions(instructions)
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
    text = instructions if instructions is not None else catalogue_instructions(slug)
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
