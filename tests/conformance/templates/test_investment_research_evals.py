"""Evals of the investment-research agents (S38-10), scored by the campaign's own guarantees.

Each case of `templates/investment-research/evals/cases.yaml` is an output an agent could write. It
is scored with the gates the workflow declares on its transition — read from `campaign.yaml` for the
strategist and from `product-memo.yaml` for the analyst (S38-15: one memo per relevant product), so
a loosened guarantee turns a `refuse` case green and this suite red — and, for the strategist, with
the one check no gate makes: no product of the campaign is named.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

TEMPLATE = Path(__file__).resolve().parents[3] / "templates" / "investment-research"
CASES = yaml.safe_load((TEMPLATE / "evals" / "cases.yaml").read_text(encoding="utf-8"))


def _gates(transition_id: str, workflow_file: str = "campaign.yaml") -> list[tuple[str, dict[str, Any]]]:
    from choregos_core.dsl import parse_workflow

    workflow, report = parse_workflow((TEMPLATE / "workflows" / workflow_file).read_text(encoding="utf-8"))
    assert workflow is not None, report
    (transition,) = [t for t in workflow.transitions if t.id == transition_id]
    return [(g.name, dict(g.params or {})) for g in transition.gates]


def named_products(output: Any, symbols: list[str]) -> list[str]:
    """The campaign's products an output names: `AAPL` for `AAPL.US`, as a word, in any case in an
    id (`qqq_trend`) and in capitals in prose (so `all` is not Allstate)."""
    text = json.dumps(output, ensure_ascii=False)
    found = []
    for symbol in symbols:
        base = symbol.split(".")[0]
        if len(base) < 2:
            continue
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(base)}(?![A-Za-z0-9])", text) or re.search(
            rf"\"id\": \"[^\"]*(?<![a-z0-9]){re.escape(base.lower())}(?![a-z0-9])", text
        ):
            found.append(base)
    return found


def _score(
    transition_id: str,
    outputs: dict[str, Any],
    documents: dict[str, Any],
    fields: dict[str, Any] | None = None,
    workflow_file: str = "campaign.yaml",
) -> list[str]:
    from choregos_contracts import StageOutputs, StageResult, StageStatus
    from choregos_core import GateContext, evaluate

    ctx = GateContext(
        result=StageResult(status=StageStatus.DONE, summary="eval", outputs=StageOutputs(**outputs)),
        documents=documents,
        fields=CASES["fields"] if fields is None else fields,
    )
    failed = []
    for name, params in _gates(transition_id, workflow_file):
        outcome = evaluate(name, ctx, params)
        if not outcome.passed:
            failed.append(f"{name}: {outcome.detail}")
    return failed


@pytest.mark.parametrize("case", CASES["strategist"], ids=lambda c: c["id"])
def test_the_strategist_cases(case: dict[str, Any]) -> None:
    refused = _score("t-strategize", {"strategies": case["strategies"]}, {})
    refused += [f"names {p}" for p in named_products(case["strategies"], CASES["fields"]["symbols"])]
    if case["expect"] == "accept":
        assert not refused, refused
    else:
        assert refused, f"{case['id']} should be refused: {case.get('why', '')}"


@pytest.mark.parametrize("case", CASES["analyst"], ids=lambda c: c["id"])
def test_the_analyst_cases(case: dict[str, Any]) -> None:
    symbol = case.get("product", "P01")
    child_fields = {  # what `t-fan-out` copies into the child of this product
        "symbol": symbol,
        "product": CASES["metrics"]["products"][symbol],
        "facts": {"trials": CASES["metrics"]["trials"]},
        **{k: CASES["fields"][k] for k in ("start", "oos_start", "end")},
    }
    refused = _score(
        "t-memo", {"memo_markdown": case["memo"]}, {}, fields=child_fields, workflow_file="product-memo.yaml"
    )
    if case["expect"] == "accept":
        assert not refused, refused
    else:
        assert refused, f"{case['id']} should be refused: {case.get('why', '')}"


def test_every_guarantee_has_a_case_it_refuses() -> None:
    """A guarantee without a refused case is not evaluated: each one must catch at least one."""
    assert {c["expect"] for c in CASES["strategist"]} == {"accept", "refuse"}
    assert {c["expect"] for c in CASES["analyst"]} == {"accept", "refuse"}
    assert [n for n, _ in _gates("t-strategize")] == ["output_schema"]
    assert [n for n, _ in _gates("t-memo", "product-memo.yaml")] == ["numbers_measured"]


def test_the_instructions_say_what_the_evals_check() -> None:
    strategist = (TEMPLATE / "agents" / "quant-strategist.yaml").read_text(encoding="utf-8")
    analyst = (TEMPLATE / "agents" / "quant-analyst.yaml").read_text(encoding="utf-8")
    assert "Never name a real product" in strategist
    assert "never quote a number" in strategist
    assert "A number found in neither fails the step" in analyst
