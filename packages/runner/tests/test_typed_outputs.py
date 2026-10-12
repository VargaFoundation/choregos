"""A declared file output (ADR 0049) is not asked for in `outputs` (S31-02).

The runner asks the agent to repair a result that lacks a declared output (S22-12). A file output
(`deck: pptx`) is written in `.choregos/outputs/`, not as a value: asking for it in `outputs` would
push the agent to write a placeholder string, which the platform would then take for the deck.
"""

from __future__ import annotations

import json
from pathlib import Path

from choregos_contracts import StageInput
from choregos_runner.result import load_result
from choregos_runner.workspace import _task_markdown, inline_outputs


def _typed(stage_input: StageInput) -> StageInput:
    stage_input.transition.outputs = ["summary_markdown", "deck"]
    stage_input.transition.output_types = {"deck": "pptx"}
    return stage_input


def test_only_inline_outputs_are_asked_for_in_the_result(stage_input: StageInput, tmp_path: Path) -> None:
    typed = _typed(stage_input)
    assert inline_outputs(typed) == ["summary_markdown"]
    path = tmp_path / "result.json"
    path.write_text(
        json.dumps({"status": "done", "summary": "deck ready", "outputs": {"summary_markdown": "# Q3"}}),
        encoding="utf-8",
    )
    assert load_result(path, inline_outputs(typed)).ok
    missing = load_result(path, list(typed.transition.outputs))
    assert not missing.ok and "`deck`" in (missing.error or "")


def test_the_task_does_not_ask_for_a_file_as_a_key_of_outputs(stage_input: StageInput) -> None:
    task = _task_markdown(_typed(stage_input))
    assert "`summary_markdown`" in task
    assert "`deck`" not in task
