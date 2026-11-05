# SPDX-License-Identifier: Apache-2.0
"""The runner's own git runs no code the agent wrote (#1345).

The agent can write `.git/hooks/*`, `.git/config` and `.git/info/attributes` from its shell: those
are the workspace, a write root of the confinement (ADR 0094), and `.git/` is not a tracked path so
the scope check does not revert it. The runner's `git commit`/`push`/`diff`/`status` then run as the
runner, OUTSIDE the confinement, and the push carries the run's git token in `GIT_CONFIG_PARAMETERS`
(`-c http.extraHeader`, S22-07). A `pre-push` hook the agent wrote ran as the runner and read the
token. These runs prove that every one of those vectors is now neutralised.
"""

from __future__ import annotations

import base64
import subprocess
from pathlib import Path
from typing import Any

import pytest
from choregos_contracts import StageInput, StageStatus
from choregos_runner.exits import Exit
from choregos_runner.runner import Runner

from .conftest import FakeInternalClient, agent_script, valid_result

pytestmark = pytest.mark.asyncio

#: A recognisable token so a `grep` can prove it reaches nowhere on disk.
GIT_TOKEN = "glpat-f1345-secret-token"
#: How `Workspace._auth_header` encodes it in `-c http.extraHeader`: the hook would see THIS form.
TOKEN_HEADER = base64.b64encode(f"x-access-token:{GIT_TOKEN}".encode()).decode()


async def _run(
    stage_input: StageInput, settings: Any, script: dict[str, Any]
) -> tuple[Any, FakeInternalClient]:
    agent_script(script, stage_input, settings.workspace)
    client = FakeInternalClient(stage_input)
    client.git_token = GIT_TOKEN
    outcome = await Runner(settings, client).execute(stage_input, client)  # type: ignore[arg-type]
    return outcome, client


def _install_hooks() -> list[str]:
    """A shell command (what the agent's shell tool runs, cwd = workspace) that drops executable
    `pre-commit` and `pre-push` hooks. Each writes a marker under `.git/`; `pre-push` also dumps its
    environment, where the token would appear through `GIT_CONFIG_PARAMETERS`."""
    return [
        "sh",
        "-c",
        'd="$PWD/.git"; mkdir -p "$d/hooks"; '
        'printf \'#!/bin/sh\\n: > "%s/pre_commit_ran"\\n\' "$d" > "$d/hooks/pre-commit"; '
        'printf \'#!/bin/sh\\n: > "%s/pre_push_ran"\\nenv > "%s/pre_push_env"\\n\' "$d" "$d" '
        '> "$d/hooks/pre-push"; '
        'chmod +x "$d/hooks/pre-commit" "$d/hooks/pre-push"',
    ]


def _install_filter_and_fsmonitor() -> list[str]:
    """A shell command that sets a `filter` and `core.fsmonitor` in `.git/config` and an attribute
    so the filter applies to every file: each runs a program that writes a marker under `.git/`."""
    return [
        "sh",
        "-c",
        'd="$PWD/.git"; '
        'cat >> "$d/config" <<EOF\n'
        '[filter "evil"]\n'
        '\tclean = sh -c ": > $d/clean_ran; cat"\n'
        '\tsmudge = sh -c ": > $d/smudge_ran; cat"\n'
        "[core]\n"
        '\tfsmonitor = sh -c ": > $d/fsmon_ran; exit 0"\n'
        "EOF\n"
        "printf '* filter=evil\\n' > \"$d/info/attributes\"",
    ]


def _git_dir(settings: Any) -> Path:
    return Path(settings.workspace) / ".git"


def _rev(repository: Path, ref: str) -> str:
    """The commit `ref` names, read with a plain git that never runs the workspace's hooks."""
    done = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "rev-parse", "--verify", ref],
        cwd=repository,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def _no_token_on_disk(root: Path) -> None:
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        blob = path.read_bytes()
        assert GIT_TOKEN.encode() not in blob, f"the raw token is on disk in {path}"
        assert TOKEN_HEADER.encode() not in blob, f"the token header is on disk in {path}"


async def test_the_runners_git_runs_no_hook_the_agent_wrote(
    stage_input: StageInput, runner_settings: Any, toy_repo: Path
) -> None:
    outcome, _client = await _run(
        stage_input,
        runner_settings,
        {
            "turns": [
                {
                    "writes": [
                        {"path": "src/orders.py", "content": "def total(lines):\n    return sum(lines) - 1\n"}
                    ],
                    "runs": [_install_hooks()],
                    "result": valid_result(),
                }
            ]
        },
    )

    assert outcome.exit_code is Exit.OK, outcome.detail
    assert outcome.result is not None and outcome.result.status is StageStatus.DONE
    # The run did commit and push: without the hooks disabled, pre-commit and pre-push WOULD fire.
    assert outcome.result.artifacts.commits, "the run committed"
    workspace = Path(runner_settings.workspace)
    assert _rev(toy_repo, "refs/heads/choregos/1-total") == _rev(workspace, "HEAD"), (
        "the push reached the remote"
    )

    git_dir = _git_dir(runner_settings)
    assert not (git_dir / "pre_commit_ran").exists(), "the pre-commit hook ran as the runner"
    assert not (git_dir / "pre_push_ran").exists(), "the pre-push hook ran as the runner"
    assert not (git_dir / "pre_push_env").exists(), "the pre-push hook dumped the runner's environment"
    _no_token_on_disk(Path(runner_settings.workspace))


async def test_the_runners_git_runs_no_filter_or_fsmonitor_the_agent_set(
    stage_input: StageInput, runner_settings: Any
) -> None:
    outcome, client = await _run(
        stage_input,
        runner_settings,
        {
            "turns": [
                {
                    "writes": [
                        {"path": "src/orders.py", "content": "def total(lines):\n    return sum(lines) - 1\n"}
                    ],
                    # A tracked file outside the scope, changed from the shell: the scope check
                    # reverts it with `git checkout <start> -- src/billing.py`, where smudge runs.
                    "runs": [
                        _install_filter_and_fsmonitor(),
                        ["sh", "-c", "echo 'RATE = 0.99' > src/billing.py"],
                    ],
                    "result": valid_result(),
                },
                {"result": valid_result("scope respected")},
            ]
        },
    )

    assert outcome.exit_code is Exit.OK, outcome.detail
    assert outcome.result is not None and outcome.result.status is StageStatus.DONE
    assert outcome.result.artifacts.commits, "the run committed (the filter did not block it either)"
    assert [e for e in client.events if e["type"] == "scope.reverted"], "the scope check ran its checkout"
    workspace = Path(runner_settings.workspace)
    assert (workspace / "src" / "billing.py").read_text(encoding="utf-8") == "RATE = 0.2\n"

    git_dir = _git_dir(runner_settings)
    # The clean filter fires on `add`/`diff`/`status`, the smudge filter on the scope check's
    # `checkout`, the fsmonitor program on any index refresh: the run goes through all three.
    assert not (git_dir / "clean_ran").exists(), "a clean filter ran as the runner"
    assert not (git_dir / "smudge_ran").exists(), "a smudge filter ran as the runner"
    assert not (git_dir / "fsmon_ran").exists(), "an fsmonitor program ran as the runner"
