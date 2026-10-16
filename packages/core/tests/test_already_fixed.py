"""A ticket whose fix is already on the default branch ends on measured evidence only (S22-47, #539).

On the dev tenant, `DiametralGroup/choregos-sandbox-dev#18` and `#21` (`dev-simple`) described bugs
already fixed on `main`: the implementation changed nothing, and the work item went on to test, review
and a pull request from a branch that did not exist. `already_fixed_proven` guards the platform's
"already fixed" exit, which the interpreter takes only after a step that measured no change.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from choregos_contracts import Evidence, StageResult, StageStatus
from choregos_core import GateContext, WorkflowEngine, evaluate
from choregos_core.dsl.parser import parse_workflow
from choregos_core.gates import ALREADY_FIXED_GATE, ALREADY_FIXED_UNPROVEN, GateOutcome, unchanged_run

DEV_SIMPLE = (
    Path(__file__).resolve().parents[3] / "templates/github-software-delivery/workflows/dev-simple.yaml"
)
MEASURED = ["diff_files", "diff_lines", "tests_failed", "tests_passed", "tests_run"]


def _result(
    *, measured: list[str] = MEASURED, commits: list[str] | None = None, **evidence: Any
) -> StageResult:
    values: dict[str, Any] = {
        "diff_files": 0,
        "diff_lines": 0,
        "tests_run": 412,
        "tests_failed": 0,
        "tests_passed": True,
        "measured": measured,
        **evidence,
    }
    result = StageResult(
        status=StageStatus.DONE, summary="Already fixed on main", evidence=Evidence(**values)
    )
    result.artifacts.commits = list(commits or [])
    return result


def _verdict(result: StageResult | None, branch_pushed: bool | None = False) -> GateOutcome:
    return evaluate(ALREADY_FIXED_GATE, GateContext(result=result, branch_pushed=branch_pushed))


def _dev_simple() -> WorkflowEngine:
    workflow, report = parse_workflow(DEV_SIMPLE.read_text(encoding="utf-8"), strict=True)
    assert report.valid, [e.format() for e in report.errors]
    return WorkflowEngine(workflow)


def test_measured_no_change_and_green_tests_on_main_prove_it() -> None:
    verdict = _verdict(_result())
    assert verdict.passed, verdict.detail
    assert "412 test(s) pass on the default branch" in verdict.detail


@pytest.mark.parametrize(
    ("result", "branch_pushed", "missing"),
    [
        (_result(measured=["tests_run", "tests_failed"]), False, "did not measure an empty change"),
        (_result(diff_files=1, diff_lines=4), False, "did not measure an empty change"),
        (_result(commits=["abc123"]), False, "did not measure an empty change"),
        (_result(), True, "a work branch was pushed"),
        (_result(), None, "could not be read"),
        (_result(measured=["diff_files", "diff_lines"]), False, "did not run the tests"),
        (_result(tests_run=0), False, "no test ran"),
        (_result(tests_failed=2, tests_passed=False), False, "2 test(s) fail"),
        (None, False, "no stage result"),
    ],
)
def test_anything_unmeasured_or_red_refuses_and_says_what(
    result: StageResult | None, branch_pushed: bool | None, missing: str
) -> None:
    verdict = _verdict(result, branch_pushed)
    assert not verdict.passed
    assert missing in verdict.detail, verdict.detail


def test_an_unchanged_run_is_measured_not_reported() -> None:
    assert unchanged_run(_result())
    assert not unchanged_run(_result(measured=[])), "the agent's zero is not a measure"
    assert not unchanged_run(_result(commits=["abc123"]))
    assert not unchanged_run(None)


def test_dev_simple_declares_the_exit_and_never_selects_it_by_default() -> None:
    engine = _dev_simple()
    assert engine.wf.metadata.version == 2
    exit_ = engine.already_fixed_exit("triaged")
    assert exit_ is not None and exit_.id == "t-already-fixed"
    assert engine.is_terminal(exit_.to) and exit_.to == "already_fixed"
    selected = engine.select_transition("triaged")
    assert selected is not None and selected.id == "t-implement"
    assert engine.already_fixed_exit("implemented") is None


def test_an_unchanged_step_ends_on_proof_and_escalates_without_it() -> None:
    engine = _dev_simple()
    implement = engine.select_transition("triaged")
    exit_ = engine.already_fixed_exit("triaged")
    assert implement is not None and exit_ is not None

    proven = engine.after_unchanged_stage(implement, exit_, [_verdict(_result())])
    assert proven.next_state == "already_fixed" and not proven.escalated
    assert proven.reason.startswith("already fixed: nothing to change")

    unproven = engine.after_unchanged_stage(
        implement, exit_, [_verdict(_result(measured=["diff_files", "diff_lines"]))]
    )
    assert unproven.next_state == "needs_human" and unproven.escalated and not unproven.retried
    assert unproven.reason.startswith(ALREADY_FIXED_UNPROVEN)
    assert "did not run the tests" in unproven.reason


def test_the_validator_refuses_an_exit_that_would_never_be_taken() -> None:
    text = DEV_SIMPLE.read_text(encoding="utf-8").replace(
        "    gates: [evidence_present]\n", "    gates: [evidence_present, already_fixed_proven]\n", 1
    )
    _, report = parse_workflow(text, strict=False)
    assert [e.code for e in report.errors] == ["gate.already_fixed_not_an_exit"]
