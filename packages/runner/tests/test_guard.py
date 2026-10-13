"""Turn and loop limits enforced by the runner (S27-07)."""

from __future__ import annotations

from typing import Any

from choregos_contracts import StageInput
from choregos_runner.acp.guard import TurnGuard
from choregos_runner.exits import Exit
from choregos_runner.runner import Runner

from .conftest import FakeInternalClient, agent_script, valid_result


def _call(n: int, command: str = "ls", kind: str = "execute") -> dict[str, Any]:
    return {"toolCallId": f"tc-{n}", "kind": kind, "rawInput": {"command": command}}


def test_the_same_call_five_times_in_a_row_is_a_loop() -> None:
    guard = TurnGuard(loop_threshold=5)
    assert [guard.observe(_call(n)) for n in range(4)] == [None] * 4
    assert guard.observe(_call(4)) == "loop"
    assert "5 times in a row" in guard.detail


def test_a_different_call_resets_the_count_and_an_update_of_the_same_call_counts_once() -> None:
    guard = TurnGuard(loop_threshold=3)
    for n, command in enumerate(["ls", "ls", "pwd", "ls", "ls"]):
        assert guard.observe(_call(n, command)) is None
    assert guard.observe(_call(4, "ls")) is None, "the same id seen again is not a new call"
    assert guard.turns == 5


def test_past_max_turns_the_agent_is_stopped() -> None:
    guard = TurnGuard(max_turns=3, loop_threshold=0)
    assert [guard.observe(_call(n, f"cmd {n}")) for n in range(4)] == [None, None, None, "max_turns"]


async def _run(stage_input: StageInput, runner_settings: Any, commands: list[str], max_turns: int) -> Any:
    looping = stage_input.model_copy(deep=True)
    looping.budget.max_turns = max_turns
    agent_script(
        {"turns": [{"commands": commands, "result": valid_result()}]}, looping, runner_settings.workspace
    )
    client = FakeInternalClient(looping)
    runner = Runner(runner_settings, client)
    outcome = await runner.execute(looping, client)  # type: ignore[arg-type]
    return outcome, client


async def test_a_looping_fake_agent_stops_with_reason_loop(
    stage_input: StageInput, runner_settings: Any
) -> None:
    """The acceptance of S27-07."""
    outcome, client = await _run(stage_input, runner_settings, ["make test"] * 8, max_turns=100)
    assert outcome.exit_code is Exit.OK, outcome.detail
    (result,) = client.results
    assert (result.status.value, result.reason) == ("failed", "loop")
    assert "same call 5 times in a row" in result.summary
    assert result.diagnostics.agent_exit == "limit"
    assert any(e["type"] == "run.stopped" for e in client.events)


async def test_an_agent_past_max_turns_stops_with_reason_max_turns(
    stage_input: StageInput, runner_settings: Any
) -> None:
    _outcome, client = await _run(stage_input, runner_settings, [f"step {n}" for n in range(6)], max_turns=3)
    (result,) = client.results
    assert result.reason == "max_turns"
    assert "more than the stage's max_turns of 3" in result.summary


async def test_an_agent_within_its_limits_is_not_stopped(
    stage_input: StageInput, runner_settings: Any
) -> None:
    _outcome, client = await _run(stage_input, runner_settings, ["a", "b", "a"], max_turns=10)
    (result,) = client.results
    assert result.status.value == "done", result.summary
