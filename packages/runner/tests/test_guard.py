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


def _announced(n: int, tool: str = "read", kind: str = "read") -> dict[str, Any]:
    """A call as opencode announces it: `pending`, its arguments not known yet."""
    return {"toolCallId": f"tc-{n}", "title": tool, "kind": kind, "status": "pending", "rawInput": {}}


def _filled(n: int, arguments: dict[str, Any], tool: str = "read", kind: str = "read") -> dict[str, Any]:
    """The `tool_call_update` that brings the call's arguments."""
    return {
        "toolCallId": f"tc-{n}",
        "title": tool,
        "kind": kind,
        "status": "in_progress",
        "rawInput": arguments,
    }


def test_reads_of_different_files_announced_without_arguments_are_not_a_loop() -> None:
    """S22-44: the verifier of sandbox-dev#4 and #6, stopped on `read:{}` after five reads of five
    different files whose `rawInput` was empty on the `tool_call` and filled on its update."""
    guard = TurnGuard(loop_threshold=5)
    files = ["task.md", "context.md", "smoke.py", "total.py", "test_total.py", "__init__.py"]
    for n in range(len(files)):
        assert guard.observe(_announced(n)) is None
    for n, path in enumerate(files):
        assert guard.observe(_filled(n, {"filePath": f"/workspace/{path}"})) is None
        assert guard.observe({"toolCallId": f"tc-{n}", "status": "completed", "title": path}) is None
    assert guard.turns == len(files)


def test_the_same_read_five_times_is_still_a_loop_once_its_arguments_are_known() -> None:
    guard = TurnGuard(loop_threshold=5)
    for n in range(4):
        assert guard.observe(_announced(n)) is None
        assert guard.observe(_filled(n, {"filePath": "/workspace/total.py"})) is None
    assert guard.observe(_announced(4)) is None, "announced, arguments unknown: not compared yet"
    assert guard.observe(_filled(4, {"filePath": "/workspace/total.py"})) == "loop"
    assert 'read:{"filePath": "/workspace/total.py"}' in guard.detail


def test_calls_that_end_without_arguments_are_never_the_same_call() -> None:
    """Unknown arguments are not identical arguments: a call that completes without them is not
    compared, and different tools of the same kind (`other`, every MCP tool) are not one tool."""
    guard = TurnGuard(loop_threshold=3)
    for n in range(6):
        assert guard.observe(_announced(n, "choregos_get_ticket", "other")) is None
        assert guard.observe({"toolCallId": f"tc-{n}", "status": "completed"}) is None
    for n, tool in enumerate(["choregos_get_ticket", "choregos_get_context", "choregos_get_ci_logs"], 10):
        assert guard.observe(_announced(n, tool, "other")) is None
        assert guard.observe(_filled(n, {"tail": 100}, tool, "other")) is None
    assert guard.tripped is None


def _shell(n: int, status: str, command: str | None = None) -> dict[str, Any]:
    """A shell call as opencode 1.18 reports it: its `rawInput` carries the working directory from
    the announcement on, and the command only once the call runs."""
    arguments: dict[str, Any] = {"cwd": "/workspace"}
    if command is not None:
        arguments["command"] = command
    return {
        "toolCallId": f"tc-{n}",
        "title": command or "bash",
        "kind": "execute",
        "status": status,
        "rawInput": arguments,
    }


def test_shell_calls_announced_with_their_cwd_only_are_not_one_call() -> None:
    """#611: the verifier of sandbox-dev#22, stopped on `execute/bash:{"cwd": "/workspace"}` after
    five different commands, each announced `pending` with the `cwd` opencode adds."""
    guard = TurnGuard(loop_threshold=5)
    for n, command in enumerate(["pytest -q", "ruff check .", "mypy src", "git diff", "pytest -x", "ls"]):
        assert guard.observe(_shell(n, "pending")) is None
        assert guard.observe(_shell(n, "in_progress", command)) is None
        assert guard.observe({"toolCallId": f"tc-{n}", "status": "completed"}) is None
    assert guard.tripped is None


def test_shell_calls_announced_together_are_compared_on_their_commands() -> None:
    """Parallel calls: every announcement comes before the first command is known."""
    guard = TurnGuard(loop_threshold=5)
    commands = ["pytest -q", "ruff check .", "mypy src", "git diff", "pytest -x"]
    for n in range(len(commands)):
        assert guard.observe(_shell(n, "pending")) is None
    for n, command in enumerate(commands):
        assert guard.observe(_shell(n, "in_progress", command)) is None
    assert guard.tripped is None


def test_the_same_shell_command_five_times_is_still_a_loop() -> None:
    guard = TurnGuard(loop_threshold=5)
    for n in range(4):
        assert guard.observe(_shell(n, "pending")) is None
        assert guard.observe(_shell(n, "in_progress", "pytest -q")) is None
    assert guard.observe(_shell(4, "pending")) is None, "announced, command unknown: not compared yet"
    assert guard.observe(_shell(4, "in_progress", "pytest -q")) == "loop"
    assert '"command": "pytest -q"' in guard.detail


def test_a_call_announced_with_all_its_arguments_is_compared_when_it_ends() -> None:
    """An agent that sends the arguments with the announcement only (claude-agent-acp): its calls
    are still compared — once they end, on what they were announced with."""
    guard = TurnGuard(loop_threshold=3)
    for n in range(2):
        assert guard.observe(_call(n) | {"status": "pending"}) is None
        assert guard.observe({"toolCallId": f"tc-{n}", "status": "completed"}) is None
    assert guard.observe(_call(2) | {"status": "pending"}) is None
    assert guard.observe({"toolCallId": "tc-2", "status": "completed"}) == "loop"


async def _run(
    stage_input: StageInput,
    runner_settings: Any,
    commands: list[str],
    max_turns: int,
    tool_calls: list[dict[str, Any]] | None = None,
) -> Any:
    looping = stage_input.model_copy(deep=True)
    looping.budget.max_turns = max_turns
    agent_script(
        {"turns": [{"commands": commands, "tool_calls": tool_calls or [], "result": valid_result()}]},
        looping,
        runner_settings.workspace,
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


def _reads(*paths: str) -> list[dict[str, Any]]:
    return [{"tool": "read", "kind": "read", "input": {"filePath": f"/workspace/{p}"}} for p in paths]


async def test_a_verifier_reading_different_files_the_opencode_way_is_not_stopped(
    stage_input: StageInput, runner_settings: Any
) -> None:
    """S22-44, end to end: through the ACP client, not only the guard."""
    paths = ["task.md", "context.md", "smoke.py", "total.py", "__init__.py", "test_total.py"]
    _outcome, client = await _run(stage_input, runner_settings, [], max_turns=100, tool_calls=_reads(*paths))
    (result,) = client.results
    assert result.status.value == "done", result.summary


def _shells(*commands: str) -> list[dict[str, Any]]:
    return [
        {
            "tool": "bash",
            "kind": "execute",
            "pending_input": {"cwd": "/workspace"},
            "input": {"command": c, "cwd": "/workspace"},
        }
        for c in commands
    ]


async def test_a_verifier_running_different_commands_the_opencode_way_is_not_stopped(
    stage_input: StageInput, runner_settings: Any
) -> None:
    """#611, end to end: shell calls announced with their `cwd` only."""
    commands = ["pytest -q", "ruff check .", "mypy src", "git diff", "pytest -x", "ls"]
    _outcome, client = await _run(
        stage_input, runner_settings, [], max_turns=100, tool_calls=_shells(*commands)
    )
    (result,) = client.results
    assert result.status.value == "done", result.summary


async def test_the_same_command_run_the_opencode_way_eight_times_is_still_a_loop(
    stage_input: StageInput, runner_settings: Any
) -> None:
    _outcome, client = await _run(
        stage_input, runner_settings, [], max_turns=100, tool_calls=_shells(*["pytest -q"] * 8)
    )
    (result,) = client.results
    assert (result.status.value, result.reason) == ("failed", "loop")


async def test_the_same_file_read_the_opencode_way_eight_times_is_still_a_loop(
    stage_input: StageInput, runner_settings: Any
) -> None:
    _outcome, client = await _run(
        stage_input, runner_settings, [], max_turns=100, tool_calls=_reads(*["total.py"] * 8)
    )
    (result,) = client.results
    assert (result.status.value, result.reason) == ("failed", "loop")
    assert "same call 5 times in a row" in result.summary
