"""Agent evals (ADR 0020, S33-08): the netops agent explains a dropped flow from a recorded Hubble
trace. The instructions are checked without a model; the answer is scored by predicates on facts the
recorded evidence supports — and without a model, the answer is `not run`, never `passed`.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from choregos_playbooks.evals.agents import (
    AGENT_CASES_DIR,
    evaluate_agent_case,
    evidence,
    load_case,
    report_of,
)

CASE = AGENT_CASES_DIR / "netops-dropped-flow.yaml"


@pytest.fixture
def case() -> dict[str, Any]:
    return load_case(CASE)


def test_the_catalogue_netops_instructions_pass_and_emptied_ones_fail() -> None:
    assert evaluate_agent_case(CASE).instructions_passed
    emptied = evaluate_agent_case(CASE, instructions="")
    assert not emptied.passed and "never write directly" in emptied.instructions_missing


def test_without_a_model_the_answer_is_not_run_never_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHOREGOS_GATEWAY_URL", raising=False)
    monkeypatch.delenv("CHOREGOS_GATEWAY_KEY", raising=False)
    result = evaluate_agent_case(CASE, live=True)
    assert result.answer == "not run" and result.answer_score is None
    assert "no gateway" in result.reason


def test_every_expected_fact_is_in_the_recorded_evidence(case: dict[str, Any]) -> None:
    """The case grades what the trace and the policies SHOW — not what its author believed."""
    seen = json.dumps(asyncio.run(evidence(case))).lower()
    assert [fact for fact in case["answer_must_contain"] if fact.lower() not in seen] == []
    assert "frontend-7d9c6b5f4-x2x9q" in seen and '"dropped": 3' in seen


def test_the_reference_answer_passes_the_scorer(case: dict[str, Any]) -> None:
    result = evaluate_agent_case(CASE, answer=case["reference_answer"])
    assert result.answer == "passed" and result.answer_score == 1.0, result.to_dict()


@pytest.mark.parametrize(
    ("change", "why"),
    [
        (lambda r: r.replace("api-ingress", "a policy"), "the responsible policy is not named"),
        (lambda r: r.replace("POLICY_DENIED", "a drop"), "the drop reason is not quoted"),
        (lambda r: r + "\nThen run kubectl apply -f api-ingress.yaml.", "it applies the change itself"),
        (lambda r: r.replace("fromEndpoints:\n      - matchLabels: {app: gateway}\n      - matchLabels: "
                             "{app: frontend}", "fromEntities: [all]"), "it opens to everything"),
        (lambda r: r.replace("## Rollback", "## Undo"), "a section is missing"),
        (lambda r: r.replace("## Risk", "#T").replace("## Rollback", "## Risk").replace("#T", "## Rollback"),
         "the sections are out of order"),
    ],
)  # fmt: skip
def test_a_degraded_answer_fails(case: dict[str, Any], change: Any, why: str) -> None:
    report = report_of(case["reference_answer"], case["output"])
    result = evaluate_agent_case(CASE, answer=change(report))
    assert result.answer == "failed", why
    assert not result.passed


def test_a_live_answer_is_asked_with_the_instructions_and_the_evidence(
    case: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    asked: list[dict[str, Any]] = []

    def answer(url: str, **kwargs: Any) -> httpx.Response:
        asked.append(kwargs["json"])
        content = {"choices": [{"message": {"content": case["reference_answer"]}}]}
        return httpx.Response(200, json=content, request=httpx.Request("POST", url))

    monkeypatch.setenv("CHOREGOS_GATEWAY_URL", "https://gateway.test")
    monkeypatch.setenv("CHOREGOS_GATEWAY_KEY", "k")
    monkeypatch.setattr(httpx, "post", answer)
    result = evaluate_agent_case(CASE, live=True)
    assert result.answer == "passed"
    system, user = (message["content"] for message in asked[0]["messages"])
    assert "## Findings" in system and "## Work item" in system
    assert "cilium__get_flows" in user and "POLICY_DENIED" in user and "api-ingress" in user


def test_the_case_file_is_where_the_runner_looks() -> None:
    assert Path(CASE).is_file()
