"""A declared file output (ADR 0049) is not asked for in `outputs` (S31-02).

The runner asks the agent to repair a result that lacks a declared output (S22-12). A file output
(`deck: pptx`) is written in `.choregos/outputs/`, not as a value: asking for it in `outputs` would
push the agent to write a placeholder string, which the platform would then take for the deck.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from choregos_contracts import ArtifactDisplay, ArtifactLevel, StageInput
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


SEVERITY = ArtifactDisplay(
    type="level",
    levels=[ArtifactLevel(value="sev3", tone="warn"), ArtifactLevel(value="sev1", tone="danger")],
)
COVERAGE = ArtifactDisplay(type="metric", unit="percent", min=0, max=100)
DISPLAYS = {"severity": SEVERITY, "coverage": COVERAGE}


def _result(tmp_path: Path, status: str = "done", **outputs: Any) -> Path:
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"status": status, "summary": "s", "outputs": outputs}), encoding="utf-8")
    return path


def test_a_value_off_its_scale_goes_back_to_the_agent_with_what_it_may_be(tmp_path: Path) -> None:
    load = load_result(_result(tmp_path, severity="catastrophic", coverage=80), displays=DISPLAYS)
    assert not load.ok
    assert "`severity`" in (load.error or "")
    assert "`sev3`" in (load.error or "") and "`sev1`" in (load.error or "")


def test_a_metric_out_of_its_bounds_goes_back_too(tmp_path: Path) -> None:
    load = load_result(_result(tmp_path, severity="sev1", coverage=140), displays=DISPLAYS)
    assert not load.ok and "at most 100" in (load.error or "")


def test_what_can_be_converted_is_accepted(tmp_path: Path) -> None:
    assert load_result(_result(tmp_path, severity="SEV1", coverage="87%"), displays=DISPLAYS).ok


def test_a_step_that_does_not_finish_owes_no_typed_value(tmp_path: Path) -> None:
    assert load_result(_result(tmp_path, "blocked", severity="nonsense"), displays=DISPLAYS).ok


def test_the_task_names_the_values_a_typed_output_may_take(stage_input: StageInput) -> None:
    stage_input.transition.outputs = ["severity", "coverage"]
    stage_input.transition.artifact_displays = DISPLAYS
    task = _task_markdown(stage_input)
    assert "- `severity`: exactly one of `sev3`, `sev1`." in task
    assert "- `coverage`: a number in percent, from 0 to 100." in task


def test_a_task_with_nothing_typed_says_nothing_of_it(stage_input: StageInput) -> None:
    assert "Typed outputs" not in _task_markdown(stage_input)


def test_an_agent_cannot_deliver_a_file_named_like_the_diff(stage_input: StageInput, tmp_path: Path) -> None:
    from choregos_runner.outputs import OUTPUTS_DIR, collect

    folder = tmp_path / OUTPUTS_DIR
    folder.mkdir(parents=True)
    (folder / "diff").write_text("nothing to see", encoding="utf-8")
    (folder / "notes.txt").write_text("kept", encoding="utf-8")
    kept, refused = collect(tmp_path, stage_input)
    assert [name for name, _, _ in kept] == ["notes.txt"]
    assert refused == ["diff: this name is measured by the platform"]
