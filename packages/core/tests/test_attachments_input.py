"""`attachments` is a reserved input: read by a step, written by none (S84-05, ADR 0197 §9)."""

from __future__ import annotations

from tranibor_core import artifact_flow, parse_workflow

HEAD = """
apiVersion: choregos/v1
kind: Workflow
metadata:
  name: inline
  version: 1
{metadata}
initial: todo
actors:
  maker: {{ type: agent, role: custom, playbook: maker }}
states:
  todo: {{ display: To do, kind: wait }}
  made: {{ display: Made }}
  done: {{ display: Done, terminal: true, kind: terminal }}
{extra}
transitions:
  - {{ id: t-read, from: todo, to: made, by: maker, inputs: {inputs}, outputs: {outputs} }}
  - {{ id: t-end, from: made, to: done, by: maker }}
"""

TAKES_FILES = "  attachments: { accept: [pdf] }"
A2A = "a2a: { skill: compare, description: Compares the quotes it is sent }"


def _text(metadata: str = "", extra: str = "", inputs: str = "[attachments]", outputs: str = "[]") -> str:
    return HEAD.format(metadata=metadata, extra=extra, inputs=inputs, outputs=outputs)


def _errors(text: str) -> dict[str, str]:
    _, report = parse_workflow(text, strict=False)
    return {issue.code: issue.message for issue in report.errors}


def test_attachments_in_outputs_is_refused_and_names_the_transition() -> None:
    errors = _errors(_text(TAKES_FILES, outputs="[attachments]"))
    assert "outputs.reserved" in errors and "t-read" in errors["outputs.reserved"]


def test_attachments_as_an_input_of_a_workflow_that_takes_no_file_is_refused() -> None:
    errors = _errors(_text())
    assert "inputs.attachments" in errors and "t-read" in errors["inputs.attachments"]


def test_metadata_attachments_or_an_a2a_block_lets_a_step_read_them() -> None:
    assert "inputs.attachments" not in _errors(_text(TAKES_FILES))
    assert "inputs.attachments" not in _errors(_text(extra=A2A))


def test_artifacts_cannot_describe_the_attachments() -> None:
    text = _text(TAKES_FILES) + "artifacts:\n  attachments: { show: tab }\n"
    assert "artifacts.attachments" in _errors(text)


def test_the_flow_origin_of_attachments_is_attached_never_missing() -> None:
    workflow, _ = parse_workflow(_text(), strict=False)
    entry = {a["name"]: a for a in artifact_flow(workflow)}["attachments"]
    assert entry["origin"] == "attached" and entry["read_by"] == ["t-read"] and entry["produced_by"] == []
