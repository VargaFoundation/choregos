# SPDX-License-Identifier: Apache-2.0
"""`python -m choregos_playbooks.evals.runner --all` — les évals qui bloquent la CI.

Deux niveaux, dans cet ordre :

1. **assertions déterministes** — le rendu du playbook contient (ou ne contient pas)
   ce qui compte. Pas de modèle, pas de réseau, pas de hasard : c'est ce qui tourne en CI.
2. **juge** — un modèle note le prompt contre une rubrique. Optionnel (`--judge`), utilisé
   la nuit, et jamais seul : un juge qui se trompe ne doit pas casser la CI.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .. import render_playbook
from . import CASES_DIR
from .agents import AgentCaseResult


@dataclass
class CaseResult:
    role: str
    passed: bool
    score: float
    missing: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    judge: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "passed": self.passed,
            "score": round(self.score, 3),
            "missing": self.missing,
            "forbidden": self.forbidden,
            "judge": self.judge,
        }


SAMPLE_CONTEXT = {
    "ticket": {
        "key": "acme/billing#123",
        "title": "Les avoirs ne sont pas déduits du total",
        "body": "Quand une commande a un avoir, le total affiché ignore la remise.",
    },
    "spec": "## Spécification\nGiven une commande avec un avoir…",
    "plan_markdown": "1. Ajouter le calcul\n2. Couvrir par un test",
    "allowed_paths": ["src/orders/**", "tests/orders/**"],
}


def evaluate_case(path: Path) -> CaseResult:
    case = yaml.safe_load(path.read_text(encoding="utf-8"))
    rendered = render_playbook(case["role"], **SAMPLE_CONTEXT)
    lowered = rendered.lower()

    missing = [needle for needle in case.get("must_contain", []) if needle.lower() not in lowered]
    forbidden = [needle for needle in case.get("must_not_contain", []) if needle.lower() in lowered]

    expected = len(case.get("must_contain", [])) or 1
    score = (expected - len(missing)) / expected
    if forbidden:
        score = 0.0
    threshold = float(case.get("min_score", 0.8))
    return CaseResult(
        role=case["role"],
        passed=score >= threshold and not forbidden,
        score=score,
        missing=missing,
        forbidden=forbidden,
    )


def judge_case(path: Path, model: str) -> dict[str, Any]:
    """Note le playbook contre sa rubrique avec un modèle, via le gateway.

    Sans gateway joignable, la fonction rend `{"skipped": ...}` : une éval de juge
    indisponible n'invalide pas les assertions déterministes.
    """
    import os

    import httpx

    case = yaml.safe_load(path.read_text(encoding="utf-8"))
    base_url = os.environ.get("CHOREGOS_GATEWAY_URL", "")
    key = os.environ.get("CHOREGOS_GATEWAY_KEY", "")
    if not base_url or not key:
        return {"skipped": "aucun gateway configuré (CHOREGOS_GATEWAY_URL / CHOREGOS_GATEWAY_KEY)"}
    rendered = render_playbook(case["role"], **SAMPLE_CONTEXT)
    rubric = "\n".join(f"- {item['critere']} (poids {item['poids']})" for item in case.get("rubric", []))
    prompt = (
        "Tu notes un prompt système destiné à un agent de code. Rends un JSON "
        '{"score": 0..1, "justification": "…"}.\n\n'
        f"## Rubrique\n{rubric}\n\n## Prompt à noter\n{rendered}"
    )
    try:
        response = httpx.post(
            f"{base_url.rstrip('/')}/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0},
            timeout=60,
        )
        content = response.json()["choices"][0]["message"]["content"]
        return dict(json.loads(content))
    except Exception as exc:  # un juge indisponible n'est pas un échec d'éval
        return {"skipped": str(exc)[:200]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Évals des playbooks Choregos")
    parser.add_argument("--all", action="store_true", help="tous les cas de `cases/`")
    parser.add_argument("--role", action="append", default=[], help="limiter à un ou plusieurs rôles")
    parser.add_argument("--judge", action="store_true", help="ajouter la notation par un modèle")
    parser.add_argument("--judge-model", default="platform/strong")
    parser.add_argument("--json", type=Path, help="écrire le rapport JSON")
    parser.add_argument(
        "--live-agents",
        action="store_true",
        help="ask the model, through the gateway, the agent cases' questions (otherwise: not run)",
    )
    args = parser.parse_args()

    paths = sorted(CASES_DIR.glob("*.yaml"))
    if args.role:
        paths = [path for path in paths if path.stem in args.role]
    if not paths:
        print("aucun cas d'éval trouvé", file=sys.stderr)
        return 1

    results: list[CaseResult] = []
    for path in paths:
        result = evaluate_case(path)
        if args.judge:
            result.judge = judge_case(path, args.judge_model)
        results.append(result)
        status = "✓" if result.passed else "✗"
        print(f"{status} {result.role:<16} score {result.score:.2f}", end="")
        if result.missing:
            print(f" · manque : {', '.join(result.missing)}", end="")
        if result.forbidden:
            print(f" · interdit présent : {', '.join(result.forbidden)}", end="")
        print()

    agent_results = _agent_cases(args) if args.all else []

    if args.json:
        report = [*(result.to_dict() for result in results), *(result.to_dict() for result in agent_results)]
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    failed = sum(not result.passed for result in results) + sum(not r.passed for r in agent_results)
    if failed:
        print(f"\n{failed} eval(s) failed: the prompt was degraded.", file=sys.stderr)
        return 1
    print(f"\n{len(results) + len(agent_results)} eval(s) green.")
    return 0


def _agent_cases(args: argparse.Namespace) -> list[AgentCaseResult]:
    """The catalogue agents' cases (ADR 0020): instructions always, the answer when it is asked for."""
    from .agents import agent_cases, evaluate_agent_case

    results = []
    for path in agent_cases():
        result = evaluate_agent_case(path, live=args.live_agents, model=args.judge_model)
        results.append(result)
        status = "✓" if result.passed else "✗"
        print(
            f"{status} agent:{result.agent:<10} instructions {'ok' if result.instructions_passed else 'KO'}",
            end="",
        )
        if result.instructions_missing:
            print(f" (missing: {', '.join(result.instructions_missing)})", end="")
        print(f" · answer {result.answer}", end="")
        if result.answer_missing or result.answer_forbidden:
            print(f" (missing: {result.answer_missing}, forbidden: {result.answer_forbidden})", end="")
        if result.reason:
            print(f" · {result.reason}", end="")
        print()
    return results


if __name__ == "__main__":
    raise SystemExit(main())
