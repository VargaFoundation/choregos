"""The runner journals what the pod's egress gate measured (S80-07f): how long the pod waited for its
egress policy, and how many direct connections got out before it held."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from choregos_contracts import StageInput
from choregos_core.egress_gate import EgressGate, wait_until_closed
from choregos_runner.exits import Exit
from choregos_runner.runner import Runner, egress_gate_report

from .conftest import FakeInternalClient, agent_script, valid_result


async def _execute(stage_input: StageInput, settings: Any) -> FakeInternalClient:
    agent_script(
        {"turns": [{"messages": ["ok"], "writes": [], "result": valid_result()}]},
        stage_input,
        settings.workspace,
    )
    client = FakeInternalClient(stage_input)
    outcome = await Runner(settings, client).execute(stage_input, client)  # type: ignore[arg-type]
    assert outcome.exit_code is Exit.OK, outcome.detail
    return client


def _report(path: Path) -> dict[str, Any]:
    """A report as the gate writes it: open twice, then refused."""
    answers = iter([True, True, False])
    now = [0.0]

    class _Socket:
        def close(self) -> None:
            return None

    def connect(target: tuple[str, int], timeout: float) -> Any:
        now[0] += 0.5
        if next(answers, False):
            return _Socket()
        raise ConnectionRefusedError(111, "refused")

    report = wait_until_closed(EgressGate(), connect=connect, clock=lambda: now[0], sleep=lambda s: None)
    path.write_text(report.to_json() + "\n", encoding="utf-8")
    payload: dict[str, Any] = json.loads(report.to_json())
    return payload


async def test_the_gates_wait_is_journaled(
    stage_input: StageInput, runner_settings: Any, tmp_path: Path
) -> None:
    written = _report(tmp_path / "report.json")
    settings = runner_settings.model_copy(update={"egress_gate_report": tmp_path / "report.json"})

    client = await _execute(stage_input, settings)

    gate = [e["payload"] for e in client.events if e["type"] == "egress.gate"]
    assert len(gate) == 1
    assert gate[0]["outcome"] == "closed"
    assert gate[0]["waited_s"] == written["waited_s"] > 0
    assert gate[0]["open_attempts"] == 2
    assert "event" not in gate[0]


async def test_a_pod_without_a_gate_journals_nothing_of_it(
    stage_input: StageInput, runner_settings: Any
) -> None:
    client = await _execute(stage_input, runner_settings)

    assert not [e for e in client.events if e["type"] == "egress.gate"]


def test_an_unreadable_report_is_said_not_hidden(tmp_path: Path) -> None:
    assert "unreadable" in egress_gate_report(tmp_path / "absent.json")["error"]
    (tmp_path / "list.json").write_text("[]")
    assert egress_gate_report(tmp_path / "list.json") == {"error": "report is not an object"}
