"""`numbers_measured` (S38-07): a memo cites the backtest's numbers, never its own."""

from __future__ import annotations

from typing import Any

from choregos_contracts import StageOutputs, StageResult, StageStatus
from choregos_core import GateContext, evaluate
from choregos_core.dsl.process import RESUMES

METRICS = {
    "products": [
        {"id": "P01", "oos": {"sharpe": 1.2349, "max_drawdown": -0.1234, "cagr": 0.0815, "trades": 37}},
        {"id": "P02", "oos": {"sharpe": 0.41, "max_drawdown": -0.3, "cagr": 0.012, "trades": 12}},
    ],
    "trials": 24,
    "best_oos_dsr": 0.962,
}
PARAMS: dict[str, Any] = {"sources": ["metrics"], "outputs": ["memo_markdown"]}


def memo(text: str, documents: dict[str, Any] | None = None, **params: Any) -> tuple[bool, str]:
    ctx = GateContext(
        result=StageResult(
            status=StageStatus.DONE,
            summary="ok",
            outputs=StageOutputs(memo_markdown=text),  # type: ignore[call-arg]
        ),
        documents={"metrics": METRICS} if documents is None else documents,
    )
    out = evaluate("numbers_measured", ctx, {**PARAMS, **params})
    return out.passed, out.detail


def test_rounded_percent_and_unsigned_quotes_pass() -> None:
    passed, detail = memo(
        "## P01\nOut of sample (2024-01-02 to 2025-06-30), Sharpe 1.23, a 12.3% drawdown, "
        "8.15 % a year over 37 trades. Strategy 2 of 3 retained; DSR 0.96 after 24 trials, in 2025."
    )
    assert passed, detail
    assert "all measured in metrics" in detail


def test_an_invented_number_fails_and_is_named() -> None:
    passed, detail = memo("P01: Sharpe 1.23, and a Sortino of 1.87.")
    assert not passed
    assert "memo_markdown: 1.87" in detail
    assert not memo("P02 returned 15% a year.")[0]
    assert not memo("P01 traded 41 times.")[0]


def test_allow_and_ignore_below_are_explicit() -> None:
    assert not memo("A year has 252 trading days.")[0]
    assert memo("A year has 252 trading days.", allow=[252])[0]
    assert not memo("Top 5 products.", ignore_below=3)[0]


def test_missing_source_or_output_fails_instead_of_passing_empty() -> None:
    assert not memo("Sharpe 1.23", documents={})[0]
    ctx = GateContext(
        result=StageResult(status=StageStatus.DONE, summary="ok"), documents={"metrics": METRICS}
    )
    assert not evaluate("numbers_measured", ctx, PARAMS).passed
    assert not evaluate("numbers_measured", ctx, {}).passed


def test_a_source_stored_as_a_json_string_is_read() -> None:
    import json

    assert memo("Sharpe 1.23", documents={"metrics": json.dumps(METRICS)})[0]


def test_the_work_item_fields_are_a_source_when_named() -> None:
    ctx = GateContext(
        result=StageResult(
            status=StageStatus.DONE,
            summary="ok",
            outputs=StageOutputs(memo_markdown="Costs of 15 bps, Sharpe 1.23."),  # type: ignore[call-arg]
        ),
        documents={"metrics": METRICS},
        fields={"cost_bps": 15},
    )
    assert not evaluate("numbers_measured", ctx, PARAMS).passed
    assert evaluate("numbers_measured", ctx, {**PARAMS, "sources": ["metrics", "fields"]}).passed


def test_process_view_says_what_it_checks() -> None:
    assert "measured" in RESUMES["numbers_measured"]
