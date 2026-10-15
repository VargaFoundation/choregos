"""As a Choregos job step (ADR 0056): paths from the workspace, results in `.choregos/job.json`."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from choregos_quant.cli import main

from .test_snapshot import fake


@pytest.fixture
def ws(workdir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ws"
    inputs = root / ".choregos" / "inputs"
    inputs.mkdir(parents=True)
    shutil.copyfile(workdir / "campaign.json", root / ".choregos" / "fields.json")
    shutil.copyfile(workdir / "bars.csv", inputs / "bars.csv")
    shutil.copyfile(workdir / "strategies.json", inputs / "strategies.json")
    monkeypatch.setenv("CHOREGOS_WORKSPACE", str(root))
    return root


def _job(root: Path) -> dict[str, object]:
    doc: dict[str, object] = json.loads((root / ".choregos" / "job.json").read_text())
    return doc


def _published(root: Path) -> set[str]:
    return {p.name for p in (root / ".choregos" / "outputs").iterdir()}


def test_a_backtest_job_publishes_metrics_inline_and_its_files(ws: Path) -> None:
    assert main(["backtest"]) == 0
    job = _job(ws)
    outputs = job["outputs"]
    assert isinstance(outputs, dict) and set(outputs) == {"metrics"}
    assert outputs["metrics"]["facts"] == job["facts"]
    assert _published(ws) == {"results.csv", "report.html"}, "result.json is never an artifact"
    assert str(job["summary"]).startswith("choregos-quant backtest: ")


def test_a_profile_job_publishes_its_two_documents(ws: Path) -> None:
    assert main(["profile"]) == 0
    outputs = _job(ws)["outputs"]
    assert isinstance(outputs, dict) and set(outputs) == {"universe_profile", "anonymisation"}


def test_a_snapshot_job_publishes_the_bars_file(ws: Path) -> None:
    assert main(["snapshot"], fetcher=fake(set())) == 0
    job = _job(ws)
    assert set(job["outputs"]) == {"snapshot"}  # type: ignore[arg-type]
    assert _published(ws) == {"bars.csv"}


def test_a_job_publishes_nothing_when_it_fails(ws: Path) -> None:
    (ws / ".choregos" / "inputs" / "strategies.json").write_text('{"strategies": [{"family": "magic"}]}')
    assert main(["backtest"]) == 2
    assert not (ws / ".choregos" / "job.json").exists()
    assert not (ws / ".choregos" / "outputs").exists()


def test_outside_a_job_the_paths_are_required(monkeypatch: pytest.MonkeyPatch, workdir: Path) -> None:
    monkeypatch.delenv("CHOREGOS_WORKSPACE", raising=False)
    assert main(["backtest"]) == 2
    assert main(["backtest", "--campaign", str(workdir / "campaign.json")]) == 2
