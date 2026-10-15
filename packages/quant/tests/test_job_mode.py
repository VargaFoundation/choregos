"""As a Choregos job step (ADR 0056): paths from the workspace, results in `.choregos/job.json`."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from choregos_quant.cli import main
from choregos_quant.snapshot import Fetched

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


def test_a_re_run_reads_the_stored_snapshot_byte_for_byte_and_fetches_nothing(ws: Path) -> None:
    """S38-04: the platform mounts what the last run of the step stored; the same request reuses it."""
    assert main(["snapshot"], fetcher=fake(set())) == 0
    inputs, outputs = ws / ".choregos" / "inputs", ws / ".choregos" / "outputs"
    first_bars = (outputs / "bars.csv").read_bytes()
    first_snapshot = _job(ws)["outputs"]["snapshot"]  # type: ignore[index]
    shutil.copyfile(outputs / "bars.csv", inputs / "bars.csv")
    (inputs / "snapshot.json").write_text(json.dumps(first_snapshot, indent=2))  # stored, not canonical

    def offline(symbol: str, start: str, end: str) -> Fetched:
        raise AssertionError(f"{symbol} fetched again")

    assert main(["snapshot"], fetcher=offline) == 0
    job = _job(ws)
    assert (outputs / "bars.csv").read_bytes() == first_bars
    assert job["outputs"]["snapshot"] == first_snapshot, "the same snapshot, its first fetch date included"  # type: ignore[index]
    assert job["facts"]["reused"] == 1  # type: ignore[index]


def test_a_stored_snapshot_is_not_reused_for_another_request_altered_bars_or_a_refresh(ws: Path) -> None:
    assert main(["snapshot"], fetcher=fake(set())) == 0
    inputs, outputs = ws / ".choregos" / "inputs", ws / ".choregos" / "outputs"
    stored = _job(ws)["outputs"]["snapshot"]  # type: ignore[index]
    calls: list[str] = []

    def counting(symbol: str, start: str, end: str) -> Fetched:
        calls.append(symbol)
        return fake(set())(symbol, start, end)

    def run(*extra: str) -> int:
        shutil.copyfile(outputs / "bars.csv", inputs / "bars.csv")
        (inputs / "snapshot.json").write_text(json.dumps(stored))
        return main(["snapshot", *extra], fetcher=counting)

    assert run("--refresh") == 0 and len(calls) == 3, "--refresh fetches"
    fields = ws / ".choregos" / "fields.json"
    campaign = json.loads(fields.read_text())
    fields.write_text(json.dumps({**campaign, "end": "2024-06-28"}))
    assert run() == 0 and len(calls) == 6, "another window: fetched"
    fields.write_text(json.dumps(campaign))
    shutil.copyfile(outputs / "bars.csv", inputs / "bars.csv")
    (inputs / "snapshot.json").write_text(json.dumps(stored))
    with (inputs / "bars.csv").open("a") as bars:
        bars.write("AAA,2020-01-04,1,1,1,1,,1\n")
    assert main(["snapshot"], fetcher=counting) == 0 and len(calls) == 9, "bars that changed: fetched"
