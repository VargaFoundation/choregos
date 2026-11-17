"""The runner does not start on the fakes in staging and prod (S80-03).

Under `CHOREGOS_FAKES` the egress control only warns (`choregos_core.egress.default_mode`). The runner
learns its environment from its pod or from its assignment (the orchestrator puts `CHOREGOS_ENV` in
both), and refuses either way, before it runs anything.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
import pytest
from choregos_adapters.sealing import key_from_log, seal
from choregos_runner import cli
from choregos_runner.assignment import AssignmentServer
from choregos_runner.exits import Exit
from choregos_runner.runner import RunOutcome
from typer.testing import CliRunner


@pytest.fixture
def environ(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """A copy of the process environment for the test to dirty, without either setting."""
    copy = {k: v for k, v in os.environ.items() if k not in {"CHOREGOS_ENV", "CHOREGOS_FAKES"}}
    monkeypatch.setattr(os, "environ", copy)
    return copy


@pytest.mark.parametrize("environment", ["staging", "prod"])
@pytest.mark.parametrize("command", [["backends"], ["run", "--run-id", "r-1"], ["serve-session"]])
def test_no_command_starts_on_the_fakes_in_production(
    environ: dict[str, str], environment: str, command: list[str]
) -> None:
    environ.update({"CHOREGOS_ENV": environment, "CHOREGOS_FAKES": "1"})
    result = CliRunner().invoke(cli.app, command)
    assert result.exit_code == int(Exit.FAKES_IN_PRODUCTION)
    assert "CHOREGOS_FAKES" in result.output and f"CHOREGOS_ENV={environment}" in result.output


@pytest.mark.parametrize("environment", ["dev", "test", ""])
def test_a_bench_keeps_its_fakes(environ: dict[str, str], environment: str) -> None:
    environ.update({"CHOREGOS_ENV": environment, "CHOREGOS_FAKES": "1"})
    result = CliRunner().invoke(cli.app, ["backends"])
    assert result.exit_code == 0, result.output


def test_production_without_the_fakes_starts(environ: dict[str, str]) -> None:
    environ.update({"CHOREGOS_ENV": "prod", "CHOREGOS_FAKES": "0"})
    assert CliRunner().invoke(cli.app, ["backends"]).exit_code == 0


async def test_a_warm_runner_refuses_an_assignment_that_says_prod_with_the_fakes_on(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], environ: dict[str, str]
) -> None:
    """The warm pool's pod carries no `CHOREGOS_ENV`: the assignment does, and nothing runs."""
    environ["CHOREGOS_FAKES"] = "true"
    ran: list[str] = []

    class _Runner:
        def __init__(self, settings: Any) -> None:
            pass

        async def run(self) -> RunOutcome:
            ran.append("run")
            return RunOutcome(Exit.OK, detail="done")

    monkeypatch.setattr(cli, "Runner", _Runner)
    started: list[AssignmentServer] = []
    real_start = AssignmentServer.start

    async def _start(self: AssignmentServer) -> None:
        self.host, self.port = "127.0.0.1", 0
        await real_start(self)
        started.append(self)

    monkeypatch.setattr(AssignmentServer, "start", _start)
    task = asyncio.create_task(cli._serve_then_run(8765, None))
    while not started:
        await asyncio.sleep(0.01)
    key = key_from_log(capsys.readouterr().out)
    secret = {"api_url": "http://api/internal", "run_token": "tok-1", "env": {"CHOREGOS_ENV": "prod"}}
    async with httpx.AsyncClient() as http:
        body = {"run_id": "r-1", "sealed": seal(key, "r-1", secret)}
        assert (await http.post(f"http://127.0.0.1:{started[0].port}/assign", json=body)).status_code == 200
    with pytest.raises(cli.typer.Exit) as stopped:
        await asyncio.wait_for(task, timeout=5)
    assert stopped.value.exit_code == int(Exit.FAKES_IN_PRODUCTION)
    assert ran == [], "the stage ran on the fakes in prod"
