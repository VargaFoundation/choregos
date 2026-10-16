# SPDX-License-Identifier: Apache-2.0
"""Evals of the catalogue's agents (ADR 0020, S33-06).

    python -m choregos_core.catalogue_d_agents.evals --all

The playbook evals (`choregos_playbooks.evals`) check the rendering of a ROLE; the catalogue's agents
carry their own instructions, which nothing checked. One case per internal agent, in `eval_cases/`:
the instructions rendered as the orchestrator renders them (sandboxed Jinja, the ticket's context
first, the case's `inputs`) must contain what makes the agent this agent, and none of what it must
never say. Deterministic: no model, no network. A case turns red when the instructions are emptied
or degraded — `packages/core/tests/test_catalogue_evals.py` proves it for every case.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..instructions import rendre_les_instructions
from . import DOSSIER, EntreeDuCatalogue, entree

CASES = DOSSIER / "eval_cases"


@dataclass
class AgentEvalResult:
    agent: str
    passed: bool
    score: float
    missing: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)


def load_case(path: Path) -> dict[str, Any]:
    return dict(yaml.safe_load(path.read_text(encoding="utf-8")))


def evaluate(case: dict[str, Any], agent: EntreeDuCatalogue | None = None) -> AgentEvalResult:
    """Score one case; `agent` replaces the catalogue's entry (a degraded copy, in the tests)."""
    agent = agent or entree(str(case["agent"]))
    if agent is None:
        return AgentEvalResult(str(case["agent"]), False, 0.0, missing=["<no such catalogue agent>"])
    rendered = rendre_les_instructions(
        str(agent.document["spec"].get("instructions", "")),
        inputs=dict(case.get("inputs") or {}),
        # What the platform says the project can measure in production (S22-48); none by default.
        production_indicators=list(case.get("production_indicators") or []),
    ).lower()
    expected = [str(n) for n in case.get("must_contain") or []]
    missing = [needle for needle in expected if needle.lower() not in rendered]
    forbidden = [str(n) for n in case.get("must_not_contain") or [] if str(n).lower() in rendered]
    score = 0.0 if forbidden else (len(expected) - len(missing)) / (len(expected) or 1)
    threshold = float(case.get("min_score", 1.0))
    return AgentEvalResult(agent.slug, score >= threshold and not forbidden, score, missing, forbidden)


def case_paths() -> list[Path]:
    return sorted(CASES.glob("*.yaml"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Evals of the catalogue's agents")
    parser.add_argument("--all", action="store_true", help="every case of `eval_cases/`")
    parser.add_argument("--agent", action="append", default=[], help="only these agents")
    args = parser.parse_args()
    paths = [p for p in case_paths() if args.all or p.stem in args.agent]
    if not paths:
        print("no agent eval case found", file=sys.stderr)
        return 1
    results = [evaluate(load_case(path)) for path in paths]
    for result in results:
        line = f"{'✓' if result.passed else '✗'} {result.agent:<20} score {result.score:.2f}"
        if result.missing:
            line += f" · missing: {', '.join(result.missing)}"
        if result.forbidden:
            line += f" · forbidden present: {', '.join(result.forbidden)}"
        print(line)
    failed = [r for r in results if not r.passed]
    if failed:
        print(f"\n{len(failed)} agent eval(s) failed: instructions were degraded.", file=sys.stderr)
        return 1
    print(f"\n{len(results)} agent eval(s) green.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
