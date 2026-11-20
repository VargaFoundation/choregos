"""`numbers_measured` (S84-16): with `identifiers: skip`, an identifier is a reference, not an amount.

Off by default: a workflow opens that hole knowingly (`EUR-99999` is an identifier to the rule).
"""

from __future__ import annotations

import pytest
from tranibor_contracts import StageOutputs, StageResult, StageStatus
from tranibor_core import GateContext, evaluate
from tranibor_core.gates import _cited

SOURCES = {"metrics": {"total": 12500.5, "rate": 0.95, "change": -0.12}}


def check(text: str, **params: str) -> bool:
    ctx = GateContext(
        result=StageResult(
            status=StageStatus.DONE,
            summary="ok",
            outputs=StageOutputs(memo_markdown=text),  # type: ignore[call-arg]
        ),
        documents=SOURCES,
    )
    return evaluate(
        "numbers_measured", ctx, {"sources": ["metrics"], "outputs": ["memo_markdown"], **params}
    ).passed


@pytest.mark.parametrize(
    "text",
    [
        "See FA-0012.",
        "Order PO-2026-0042 shipped",
        "contract CTR_17 applies",
        "certified ISO-9001",
        "release v2.3.1 is out",
        "path a/b-123/c",
    ],
)
def test_an_identifier_is_not_cited(text: str) -> None:
    assert _cited(text, skip_identifiers=True) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("-12%", [-0.12]),
        ("12,500.50", [12500.5]),
        ("0.95", [0.95]),
        ("3 suppliers", [3.0]),
        ("Q3 2026", [2026.0]),  # `Q3` is a word with a digit: read as nothing today, and still nothing
        ("FA-0012 and 12,500.50 at 0.95", [12500.5, 0.95]),
    ],
)
def test_a_standalone_number_keeps_its_reading(text: str, expected: list[float]) -> None:
    assert [v for _w, v in _cited(text, skip_identifiers=True)] == expected


def test_a_memo_citing_an_identifier_beside_measured_amounts_passes_when_the_workflow_says_so() -> None:
    memo = "Under FA-0012 the total is 12,500.50 with a 95% rate and a -12% change."
    assert check(memo, identifiers="skip")
    assert not check(memo), "by default the digits of FA-0012 are still read, and 12 was not measured"


WORKFLOW = """\
apiVersion: choregos/v1
kind: Workflow
metadata: {{name: memo, version: 1}}
actors:
  writer: {{type: agent, role: writer}}
states:
  inbox: {{display: Inbox}}
  done: {{display: Done, terminal: true}}
transitions:
  - {{id: t1, from: inbox, to: done, by: writer, outputs: [memo_markdown], gates: [{gate}]}}
defaults:
  from_any_agent_state: {{on_question: inbox}}
"""


def test_the_validator_refuses_an_unknown_identifiers_value() -> None:
    from tranibor_core.dsl import parse_workflow

    for value, valid in (("skip", True), ("read", True), ("ignore", False)):
        gate = f"{{name: numbers_measured, params: {{sources: [fields], identifiers: {value}}}}}"
        _wf, report = parse_workflow(WORKFLOW.format(gate=gate), strict=False)
        codes = [i.code for i in report.errors]
        assert ("gate.numbers_measured" not in codes) is valid, (value, codes)
