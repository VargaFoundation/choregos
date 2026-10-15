"""The agent reads its repository with its own git (S22-33, choregos-sandbox-dev#13).

The pod volume belongs to root and the agent runs as 1000: without `safe.directory`, its
`git status` dies with "detected dubious ownership". The runner only protected its own commands.
"""

from __future__ import annotations

import functools
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest
from choregos_contracts import StageInput
from choregos_runner.exits import Exit
from choregos_runner.runner import Runner
from choregos_runner.workspace import agent_git_env

from .conftest import FakeInternalClient, agent_script, valid_result

# git's own test hook (since 2.35.2): it treats the repository as someone else's, without root.
FOREIGN_OWNER = {"GIT_TEST_ASSUME_DIFFERENT_OWNER": "1"}


def test_the_workspace_is_safe_when_nothing_is_configured(tmp_path: Path) -> None:
    assert agent_git_env({}, tmp_path) == {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "safe.directory",
        "GIT_CONFIG_VALUE_0": str(tmp_path),
    }


def test_an_existing_configuration_is_kept(tmp_path: Path) -> None:
    existing = {
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "core.quotepath",
        "GIT_CONFIG_VALUE_0": "off",
        "GIT_CONFIG_KEY_1": "user.name",
        "GIT_CONFIG_VALUE_1": "agent",
    }
    assert agent_git_env(existing, tmp_path) == {
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_2": "safe.directory",
        "GIT_CONFIG_VALUE_2": str(tmp_path),
    }


def test_nothing_is_added_when_the_workspace_is_already_safe(tmp_path: Path) -> None:
    already = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "safe.directory",
        "GIT_CONFIG_VALUE_0": str(tmp_path),
    }
    assert agent_git_env(already, tmp_path) == {}


def test_an_unreadable_count_is_not_rewritten(tmp_path: Path) -> None:
    assert agent_git_env({"GIT_CONFIG_COUNT": "two"}, tmp_path) == {}


def test_the_environment_carries_nothing_else(tmp_path: Path) -> None:
    """No widening: no token, no identity, no `GIT_ASKPASS` — only `GIT_CONFIG_*` keys."""
    added = agent_git_env({}, tmp_path)
    assert all(key.startswith("GIT_CONFIG_") for key in added)


def _refused(path: Path) -> subprocess.CompletedProcess[str]:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return subprocess.run(
        ["git", "status"],
        cwd=path,
        env={"PATH": "/usr/bin:/bin", **FOREIGN_OWNER},
        capture_output=True,
        text=True,
        check=False,
    )


@functools.cache
def _hook_works() -> bool:
    """Whether this machine's git honours the hook. CI's does not (an older git, or a root user,
    whom git trusts): there the end-to-end runs below cannot reproduce the refusal and are skipped,
    and the unit tests of `agent_git_env` above remain the guard."""
    with tempfile.TemporaryDirectory() as path:
        return "dubious ownership" in _refused(Path(path)).stderr


needs_the_hook = pytest.mark.skipif(
    not _hook_works(), reason="this git does not honour GIT_TEST_ASSUME_DIFFERENT_OWNER"
)


@needs_the_hook
def test_git_hook_reproduces_the_refusal(tmp_path: Path) -> None:
    """The control: without `safe.directory`, the hook is enough to have the repository refused."""
    assert "dubious ownership" in _refused(tmp_path).stderr


async def _agent_run(stage_input: StageInput, runner_settings: Any, env: dict[str, str]) -> str:
    """A run where the fake agent runs `git status` and reads a config key; returns what it saw."""
    agent_script(
        {
            "turns": [
                {
                    "runs": [
                        ["git", "status", "--short", "--branch"],
                        ["git", "config", "--get", "core.choregostest"],
                    ],
                    "result": valid_result(summary="seen: {grep}"),
                }
            ]
        },
        stage_input,
        runner_settings.workspace,
    )
    stage_input.agent.launch.env.update({**FOREIGN_OWNER, **env})
    client = FakeInternalClient(stage_input)
    outcome = await Runner(runner_settings, client).execute(stage_input, client)  # type: ignore[arg-type]
    assert outcome.exit_code is Exit.OK, outcome.detail
    return client.results[0].summary


@needs_the_hook
async def test_the_agent_reads_its_repository(stage_input: StageInput, runner_settings: Any) -> None:
    seen = await _agent_run(stage_input, runner_settings, {})
    assert "dubious ownership" not in seen, seen
    assert "## " in seen, f"git status answered: {seen}"


@needs_the_hook
async def test_the_agent_keeps_its_own_configuration(stage_input: StageInput, runner_settings: Any) -> None:
    seen = await _agent_run(
        stage_input,
        runner_settings,
        {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.choregostest", "GIT_CONFIG_VALUE_0": "kept"},
    )
    assert "dubious ownership" not in seen, seen
    assert "## " in seen and "kept" in seen, seen
