# SPDX-License-Identifier: Apache-2.0
"""`tranibor-procurement extract` as a job step: its exit codes and the `job.json` the runner reads."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from tranibor_contracts import StageInput, StageStatus
from tranibor_procurement import job
from tranibor_procurement.cli import main
from tranibor_runner.job import JobRun, job_result

SRC = Path(__file__).resolve().parents[1] / "src"


def _job(root: Path) -> dict[str, object]:
    doc: dict[str, object] = json.loads((root / ".choregos" / "job.json").read_text(encoding="utf-8"))
    return doc


def test_help_works_with_no_input_and_no_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("TRANIBOR_WORKSPACE", raising=False)
    for argv in (["--help"], ["extract", "--help"]):
        done = subprocess.run(
            [sys.executable, "-m", "tranibor_procurement.cli", *argv],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env={"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin"},
            check=False,
        )
        assert done.returncode == 0, done.stderr
        assert "extract" in done.stdout
    assert "--max-chars-per-file" in done.stdout and "--timeout" in done.stdout


def test_a_job_publishes_job_json_that_the_runner_reads(
    hand: Callable[..., Path], stage_input: StageInput
) -> None:
    root = hand()
    assert main(["extract"]) == 0
    stage, evidence = job_result(stage_input, root, JobRun(0, 0.1, "", ""))
    assert stage.status is StageStatus.DONE, stage.summary
    assert stage.reason is None
    assert evidence.facts == {
        "files_total": 7,
        "files_read": 5,
        "files_unreadable": 2,
        "files_truncated": 0,
        "notation_ambiguous": 0,
    }
    assert (
        "locked-offer.pdf (encrypted)" in stage.summary and "scan-only.pdf (no_text_layer)" in stage.summary
    )
    offers = stage.outputs.model_dump()["offers_text"]
    assert offers["files"][0]["name"] == "alderbrook-lines.csv"
    doc = _job(root)
    assert set(doc) == {"summary", "facts", "outputs"} and set(doc["outputs"]) == {"offers_text"}  # type: ignore[arg-type]


def test_a_job_whose_files_are_all_unreadable_still_exits_zero_and_says_so(
    hand: Callable[..., Path], stage_input: StageInput
) -> None:
    root = hand("scan-only.pdf", "locked-offer.pdf")
    assert main(["extract"]) == 0
    doc = _job(root)
    assert doc["facts"]["files_read"] == 0 and doc["facts"]["files_unreadable"] == 2  # type: ignore[index]
    stage, _ = job_result(stage_input, root, JobRun(0, 0.1, "", ""))
    assert stage.status is StageStatus.DONE, (
        "the step is done: the workflow's gate decides what unreadable means"
    )


def test_the_field_number_format_reaches_the_extraction(hand: Callable[..., Path]) -> None:
    root = hand("brightfield-offer.docx")
    (root / ".choregos" / "fields.json").write_text(json.dumps({"number_format": "decimal_point"}))
    assert main(["extract"]) == 0
    files = _job(root)["outputs"]["offers_text"]["files"]  # type: ignore[index]
    assert files[0]["notation"] == "decimal_point" and "11 500,00 EUR" in files[0]["text"]


def test_an_unknown_number_format_field_breaks_the_contract(
    hand: Callable[..., Path], capsys: pytest.CaptureFixture[str]
) -> None:
    root = hand("alderbrook-lines.csv")
    (root / ".choregos" / "fields.json").write_text(json.dumps({"number_format": "european"}))
    assert main(["extract"]) == 2
    assert "number_format" in capsys.readouterr().err
    assert not (root / ".choregos" / "job.json").exists()


def test_no_inputs_directory_breaks_the_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TRANIBOR_WORKSPACE", str(tmp_path))
    assert main(["extract"]) == 2
    assert "no inputs directory" in capsys.readouterr().err
    assert not (tmp_path / ".choregos" / "job.json").exists()


def test_an_output_past_the_inline_bound_breaks_the_contract_and_writes_nothing(
    hand: Callable[..., Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = hand("alderbrook-offer.pdf")
    monkeypatch.setattr(job, "MAX_INLINE_BYTES", 200)
    assert main(["extract"]) == 2
    assert "inline output" in capsys.readouterr().err
    assert not (root / ".choregos" / "job.json").exists()


def test_the_options_reach_the_extraction(hand: Callable[..., Path]) -> None:
    root = hand("alderbrook-offer.pdf")
    assert main(["extract", "--max-chars-per-file", "50", "--timeout", "30"]) == 0
    doc = _job(root)
    assert doc["facts"]["files_truncated"] == 1  # type: ignore[index]
    assert len(doc["outputs"]["offers_text"]["files"][0]["text"]) == 50  # type: ignore[index]
