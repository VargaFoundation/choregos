# SPDX-License-Identifier: Apache-2.0
"""An agent's eval suite (S32-03, ADR 0020 level 1): deterministic cases, a publish gate.

A case renders the version's instructions — in the sandbox, as a run would — on its sample input,
and checks that the text contains what it must and nothing it must not. No model, no network, no
chance: the gate is the same every time, as `make evals` is for the playbooks.

The suite that gates a new version is the published version's cases AND the new version's: a case
leaves the suite only through a version that still passes it, so a failing case cannot be deleted
to get a version through.
"""

from __future__ import annotations

from collections.abc import Iterable

from choregos_core.instructions import rendre_les_instructions
from jinja2 import TemplateError

from ..errors import ApiError
from ..schemas import AgentEvalCase, AgentEvalResult, AgentSpec


def evaluate(spec: AgentSpec, cases: Iterable[AgentEvalCase]) -> list[AgentEvalResult]:
    """Each case, played on the version's instructions."""
    results: list[AgentEvalResult] = []
    for case in cases:
        sample = case.input
        ticket = {"key": "EVAL-1", "title": sample.title, "body": sample.body}
        try:
            text = rendre_les_instructions(spec.instructions, ticket=ticket)
        except TemplateError as error:
            results.append(
                AgentEvalResult(name=case.name, passed=False, error=f"{type(error).__name__}: {error}")
            )
            continue
        missing = [m for m in case.must_contain if m not in text]
        forbidden = [f for f in case.must_not_contain if f in text]
        results.append(
            AgentEvalResult(
                name=case.name, passed=not missing and not forbidden, missing=missing, forbidden=forbidden
            )
        )
    return results


def suite_for(new: AgentSpec, published: AgentSpec | None) -> list[AgentEvalCase]:
    """The published version's cases, then the new version's."""
    return [*(published.evals if published else []), *new.evals]


def gate(new: AgentSpec, published: AgentSpec | None) -> list[AgentEvalResult]:
    """Refuse (409) a version that fails a case of its suite; the results otherwise."""
    results = evaluate(new, suite_for(new, published))
    failing = [r for r in results if not r.passed]
    if failing:
        raise ApiError(
            409,
            "Eval suite failed",
            f"{len(failing)} case(s) of the agent's eval suite fail: the version is not published",
            errors=[
                {
                    "loc": ["evals", r.name],
                    "msg": r.error
                    or "; ".join(
                        [*(f"missing “{m}”" for m in r.missing), *(f"contains “{f}”" for f in r.forbidden)]
                    ),
                    "type": "eval_failed",
                }
                for r in failing
            ],
        )
    return results
