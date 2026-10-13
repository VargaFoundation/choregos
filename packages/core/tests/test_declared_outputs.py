"""Every declared output is required, whatever its type (S31-02, ADR 0049).

Before, a `done` result that lacked a declared output advanced unless the transition carried an
`outputs_present` gate; only the runner's repair loop asked for it, and a runner that skipped it (an
older image, a fake executor, a result posted by hand) let the stage pass on the agent's word.
"""

from __future__ import annotations

import pytest
from choregos_contracts import ArtifactFile, Artifacts, StageResult, StageStatus, missing_outputs
from choregos_contracts.workflow import Transition
from choregos_core import GateContext, ValidationError, WorkflowEngine, evaluate, parse_workflow

WORKFLOW = """
apiVersion: choregos/v1
kind: Workflow
metadata: { name: deck, version: 1 }
actors:
  presenter: { type: agent, role: refine }
  owner: { type: human, group: sales }
states:
  request: { display: Request }
  presented: { display: Presented }
  stuck: { display: Stuck, kind: wait }
  done: { display: Done, terminal: true }
transitions:
  - id: t-present
    from: request
    to: presented
    by: presenter
    outputs: [summary_markdown, budget, deck]
    output_types: { budget: xlsx, deck: pptx }
    on_fail: { to: request, max_attempts: 2, escalate_to: stuck }
  - { id: t-close, from: presented, to: done, by: presenter }
  - { id: t-unstick, from: stuck, to: request, by: owner }
defaults:
  from_any_agent_state: { on_question: stuck }
"""

DIGEST = "sha256:" + "0" * 64


def _file(name: str, type_: str) -> ArtifactFile:
    return ArtifactFile(
        filename=f"{name}.{type_}", type=type_, media_type="application/zip", digest=DIGEST, size=3
    )


def _result(outputs: dict[str, object], files: dict[str, ArtifactFile] | None = None) -> StageResult:
    return StageResult(
        status=StageStatus.DONE,
        summary="deck ready",
        outputs=outputs,
        artifacts=Artifacts(files=files or {}),
    )


@pytest.fixture
def engine() -> WorkflowEngine:
    workflow, report = parse_workflow(WORKFLOW, strict=False)
    assert report.valid, [i.format() for i in report.errors]
    return WorkflowEngine(workflow)


def _present(engine: WorkflowEngine) -> Transition:
    transition = engine.select_transition("request")
    assert transition is not None
    return transition


def test_a_missing_inline_output_fails_the_stage_without_any_gate(engine: WorkflowEngine) -> None:
    transition = _present(engine)
    files = {"budget": _file("budget", "xlsx"), "deck": _file("deck", "pptx")}
    decision = engine.after_stage(transition, _result({}, files), attempts=0)
    assert decision.next_state == "request" and decision.retried
    assert "missing declared outputs: summary_markdown" in decision.reason


def test_a_missing_xlsx_fails_the_stage_even_with_every_value_written(engine: WorkflowEngine) -> None:
    transition = _present(engine)
    result = _result({"summary_markdown": "# Q3", "budget": "see attached"}, {"deck": _file("deck", "pptx")})
    decision = engine.after_stage(transition, result, attempts=0)
    assert decision.next_state == "request"
    assert decision.reason.endswith("missing declared outputs: budget"), (
        "a value named like a file output does not stand for the file"
    )


def test_the_last_attempt_escalates(engine: WorkflowEngine) -> None:
    decision = engine.after_stage(_present(engine), _result({}), attempts=2)
    assert decision.next_state == "stuck" and decision.escalated


def test_every_output_present_moves_forward(engine: WorkflowEngine) -> None:
    files = {"budget": _file("budget", "xlsx"), "deck": _file("deck", "pptx")}
    decision = engine.after_stage(_present(engine), _result({"summary_markdown": "# Q3"}, files), attempts=0)
    assert decision.next_state == "presented"


def test_a_history_recorded_before_the_rule_keeps_its_old_decision(engine: WorkflowEngine) -> None:
    decision = engine.after_stage(_present(engine), _result({}), attempts=0, require_outputs=False)
    assert decision.next_state == "presented"


def test_missing_outputs_reads_values_and_files_apart() -> None:
    outputs, types = ["a", "b", "c"], {"b": "png", "c": "json"}
    result = _result({"a": "x", "b": "not a file", "c": []}, {})
    assert missing_outputs(outputs, types, result) == ["b", "c"]
    assert missing_outputs(outputs, types, None) == outputs


def test_outputs_present_sees_a_stored_file() -> None:
    files = {"budget": _file("budget", "xlsx"), "deck": _file("deck", "pptx")}
    ctx = GateContext(
        result=_result({"summary_markdown": "# Q3"}, files),
        expected_outputs=["summary_markdown", "budget", "deck"],
    )
    assert evaluate("outputs_present", ctx, {}).passed
    ctx.result = _result({"summary_markdown": "# Q3"}, {"deck": files["deck"]})
    outcome = evaluate("outputs_present", ctx, {})
    assert not outcome.passed and outcome.annotations == ["budget"]


def _codes(text: str) -> list[str]:
    try:
        _workflow, report = parse_workflow(text, strict=False)
    except ValidationError as exc:
        return [i.code for i in exc.issues]
    return [i.code for i in report.errors]


def test_a_type_for_an_undeclared_output_is_refused() -> None:
    text = WORKFLOW.replace("output_types: { budget: xlsx, deck: pptx }", "output_types: { video: mp4 }")
    assert "output_types.undeclared" in _codes(text)


def test_an_unknown_type_is_refused() -> None:
    text = WORKFLOW.replace("deck: pptx }", "deck: keynote }")
    codes = _codes(text)
    assert any(code.startswith(("output_types.unknown", "schema.")) for code in codes), codes


def test_a_typed_name_that_cannot_be_a_file_name_is_refused() -> None:
    text = WORKFLOW.replace(
        "outputs: [summary_markdown, budget, deck]", "outputs: [summary_markdown, budget, ../deck]"
    )
    text = text.replace("deck: pptx }", "../deck: pptx }")
    assert "output_types.name" in _codes(text)
