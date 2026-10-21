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


def _child_memo(text: str, parent: dict[str, Any] | None, fields: dict[str, Any]) -> tuple[bool, str]:
    ctx = GateContext(
        result=StageResult(
            status=StageStatus.DONE,
            summary="ok",
            outputs=StageOutputs(memo_markdown=text),  # type: ignore[call-arg]
        ),
        fields=fields,
        parent_documents=parent,
    )
    out = evaluate(
        "numbers_measured",
        ctx,
        {"sources": ["parent.metrics.products.$symbol"], "outputs": ["memo_markdown"]},
    )
    return out.passed, out.detail


PARENT = {"metrics": {"products": {"A.US": {"sharpe": 1.23}, "B.US": {"sharpe": 0.55}}}}


def test_parent_source_reads_the_parents_measure_of_this_product_only() -> None:
    assert _child_memo("Sharpe 1.23.", PARENT, {"symbol": "A.US"})[0]
    assert not _child_memo("Sharpe 0.55.", PARENT, {"symbol": "A.US"})[0], "a sibling's number"


def test_parent_source_ignores_what_the_childs_fields_say() -> None:
    fields = {"symbol": "A.US", "product": {"sharpe": 9.87}}
    assert not _child_memo("Sharpe 9.87.", PARENT, fields)[0]


def test_parent_source_refuses_a_work_item_with_no_parent() -> None:
    passed, detail = _child_memo("Sharpe 1.23.", None, {"symbol": "A.US"})
    assert not passed and "no parent" in detail


THRESHOLDS = {"metrics": {"thresholds": {"min_dsr": 0.0, "max_drawdown": 0.35, "min_trades": 1}}}


def test_must_quote_refuses_a_memo_that_leaves_out_the_bar() -> None:
    """#824: a memo of a product judged against a lowered bar has to say so."""
    params = {"must_quote": ["thresholds"]}
    docs = {"metrics": METRICS, "thresholds": THRESHOLDS["metrics"]["thresholds"]}
    passed, detail = memo("P01 passed: Sharpe 1.23 out of sample.", docs, **params)
    assert not passed
    assert "to quote written nowhere" in detail and "thresholds: 0.35" in detail and "thresholds: 1" in detail
    stated = (
        "P01 passed against a minimum DSR of 0, a maximum drawdown of 35% and at least 1 trade: Sharpe 1.23."
    )
    assert memo(stated, docs, **params)[0]


def test_must_quote_is_a_source_too_and_checks_the_bar_as_applied() -> None:
    docs = {"metrics": METRICS, "thresholds": THRESHOLDS["metrics"]["thresholds"]}
    # The bar is quoted, so it is no invented number; a bar the engine did not apply is.
    assert memo("Bar: DSR 0, drawdown 35%, 1 trade.", docs, must_quote=["thresholds"])[0]
    passed, detail = memo("Bar: DSR 0.95, drawdown 35%, 1 trade.", docs, must_quote=["thresholds"])
    assert not passed and "memo_markdown: 0.95" in detail and "thresholds: 0" in detail


def test_must_quote_reads_the_parents_thresholds_not_the_childs_fields() -> None:
    ctx = GateContext(
        result=StageResult(
            status=StageStatus.DONE,
            summary="ok",
            outputs=StageOutputs(memo_markdown="Judged against DSR 0.95, drawdown 35%, 10 trades."),  # type: ignore[call-arg]
        ),
        fields={"symbol": "A.US", "thresholds": {"min_dsr": 0.95, "max_drawdown": 0.35, "min_trades": 10}},
        parent_documents=THRESHOLDS,
    )
    out = evaluate(
        "numbers_measured", ctx, {"must_quote": ["parent.metrics.thresholds"], "outputs": ["memo_markdown"]}
    )
    assert not out.passed, "the child's fields say 0.95; the engine applied 0"


def test_must_quote_wants_the_bar_written_not_rounded_to() -> None:
    """A "1" rounds to 0.95: as a quote, it would let a memo leave out a DSR bar of 0.95."""
    docs = {"metrics": METRICS, "thresholds": {"min_dsr": 0.95, "max_drawdown": 0.35, "min_trades": 10}}
    passed, detail = memo(
        "Sharpe 1.23 over 10 trades, a drawdown under 35%; rank 1.", docs, must_quote=["thresholds"]
    )
    assert not passed and "thresholds: 0.95" in detail
    assert memo("Bar: DSR 0.95, drawdown 35%, 10 trades; Sharpe 1.23.", docs, must_quote=["thresholds"])[0]
