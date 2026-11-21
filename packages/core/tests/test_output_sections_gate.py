"""`output_sections`: the Markdown output of a stage carries its sections, with no repository."""

from __future__ import annotations

from tranibor_contracts import StageOutputs, StageResult, StageStatus
from tranibor_core import GateContext, evaluate, parse_workflow
from tranibor_core.gates import gate_needs

MEMO = "# Memo\n\n## Summary\ntext\n\n## Risks\ntext\n\n## Next steps\ntext\n"
PARAMS = {"outputs": {"memo_markdown": ["Summary", "Risks", "Next steps"]}}


def produced(**outputs: object) -> GateContext:
    return GateContext(
        result=StageResult(status=StageStatus.DONE, summary="ok", outputs=StageOutputs(**outputs))  # type: ignore[arg-type]
    )


def test_every_section_present_passes() -> None:
    assert evaluate("output_sections", produced(memo_markdown=MEMO), PARAMS).passed


def test_a_missing_section_fails_and_is_named() -> None:
    out = evaluate("output_sections", produced(memo_markdown=MEMO.replace("## Risks", "")), PARAMS)
    assert not out.passed
    assert "memo_markdown: section “Risks” missing" in out.detail
    assert "Summary" not in out.detail


def test_a_level_three_heading_counts() -> None:
    memo = "### summary\n\n#### Risks\n\n### NEXT STEPS\n"
    assert evaluate("output_sections", produced(memo_markdown=memo), PARAMS).passed


def test_ordered_refuses_the_right_sections_in_the_wrong_order() -> None:
    memo = "## Risks\n\n## Summary\n\n## Next steps\n"
    assert evaluate("output_sections", produced(memo_markdown=memo), PARAMS).passed
    out = evaluate("output_sections", produced(memo_markdown=memo), {**PARAMS, "ordered": True})
    assert not out.passed
    assert "out of order" in out.detail
    assert evaluate("output_sections", produced(memo_markdown=MEMO), {**PARAMS, "ordered": True}).passed


def test_an_absent_output_fails_and_is_named() -> None:
    out = evaluate("output_sections", produced(other="x"), PARAMS)
    assert not out.passed and "memo_markdown: output absent" in out.detail
    assert not evaluate("output_sections", GateContext(), PARAMS).passed
    assert not evaluate("output_sections", produced(memo_markdown=MEMO), {}).passed


def test_a_json_output_fails() -> None:
    out = evaluate("output_sections", produced(memo_markdown={"Summary": "x"}), PARAMS)
    assert not out.passed and "not text" in out.detail


def test_a_pattern_is_required_per_output() -> None:
    params = {**PARAMS, "patterns": {"memo_markdown": [r"\d+ %"]}}
    assert not evaluate("output_sections", produced(memo_markdown=MEMO), params).passed
    assert evaluate("output_sections", produced(memo_markdown=MEMO + "12 %"), params).passed


def test_the_gate_needs_no_connector() -> None:
    assert gate_needs("output_sections") == frozenset()


WORKFLOW = """\
apiVersion: choregos/v1
kind: Workflow
metadata: {{name: memo, version: 1}}
actors:
  writer: {{type: agent, role: writer}}
  ci: {{type: system}}
  owner: {{type: human, group: po}}
states:
  inbox: {{display: Inbox}}
  done: {{display: Done, terminal: true}}
transitions:
  - {{id: t1, from: inbox, to: done, by: writer, outputs: [memo_markdown], gates: [{gate}]}}
defaults:
  from_any_agent_state: {{on_question: inbox}}
"""


def _codes(gate: str) -> list[str]:
    _wf, report = parse_workflow(WORKFLOW.format(gate=gate), strict=False)
    return [i.code for i in report.errors]


def test_the_validator_accepts_a_declared_output() -> None:
    ok = "{name: output_sections, params: {outputs: {memo_markdown: [Summary]}}}"
    assert _codes(ok) == []


def test_the_validator_refuses_an_undeclared_output_name() -> None:
    bad = "{name: output_sections, params: {outputs: {letter_markdown: [Summary]}}}"
    assert "gate.output_sections" in _codes(bad)


def test_the_validator_refuses_empty_outputs() -> None:
    assert "gate.nothing_to_check" in _codes("{name: output_sections, params: {outputs: {}}}")
    assert "gate.nothing_to_check" in _codes("output_sections")


def test_the_validator_refuses_a_pattern_that_does_not_compile() -> None:
    params = "{outputs: {memo_markdown: [Summary]}, patterns: {memo_markdown: ['(']}}"
    bad = f"{{name: output_sections, params: {params}}}"
    assert "gate.output_sections" in _codes(bad)
