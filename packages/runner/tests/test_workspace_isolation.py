# SPDX-License-Identifier: Apache-2.0
"""A workspace never touches a repository it did not create (S40-01i, finding #891).

Everything runs in throwaway repositories under `tmp_path`: the real checkout is never involved.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from choregos_contracts import StageInput
from choregos_runner.workspace import Workspace, WorkspaceError, run_command

pytestmark = pytest.mark.asyncio


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return done.stdout.strip()


@pytest.fixture
def linked_worktree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A throwaway repository and a linked worktree of it, in an isolated git environment."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q", "--initial-branch", "main")
    identity = ["-c", "user.name=dev", "-c", "user.email=dev@example.org"]
    _git(main, *identity, "commit", "-q", "--allow-empty", "-m", "i")
    tree = tmp_path / "tree"
    _git(main, "worktree", "add", "-q", "--detach", str(tree))
    return main, tree


def _snapshot(main: Path, tree: Path) -> tuple[str, str, str]:
    return (
        (main / ".git" / "config").read_text(encoding="utf-8"),
        _git(tree, "rev-parse", "HEAD"),
        _git(main, "rev-parse", "HEAD"),
    )


async def test_inherited_git_dir_does_not_reach_the_enclosing_repository(
    linked_worktree: tuple[Path, Path],
    stage_input: StageInput,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The #891 path: a parent exported `GIT_DIR` (a hook, a worktree's tooling); the runner's
    `git init` made the developer's repository bare and `git config` wrote the bot's identity."""
    main, tree = linked_worktree
    before = _snapshot(main, tree)
    monkeypatch.setenv("GIT_DIR", _git(tree, "rev-parse", "--absolute-git-dir"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tree))

    workspace = Workspace(tree / "runs" / "r1")
    await workspace.prepare(stage_input.model_copy(update={"repo": None}))

    assert _snapshot(main, tree) == before, "the enclosing repository was modified"
    assert (tree / "runs" / "r1" / ".git").is_dir(), "the workspace has its own repository"
    top = Path((await workspace.git("rev-parse", "--show-toplevel")).stdout.strip())
    assert top.resolve() == (tree / "runs" / "r1").resolve()


async def test_a_workspace_without_a_repository_of_its_own_is_refused(
    linked_worktree: tuple[Path, Path], stage_input: StageInput
) -> None:
    """A directory with a broken `.git` inside a checkout: git must not climb to the checkout."""
    main, tree = linked_worktree
    before = _snapshot(main, tree)
    path = tree / "runs" / "r2"
    (path / ".git").mkdir(parents=True)  # exists, so `init` is skipped, yet it is no repository

    with pytest.raises(WorkspaceError, match="not the workspace's own"):
        await Workspace(path).prepare(stage_input.model_copy(update={"repo": None}))

    assert _snapshot(main, tree) == before


async def test_a_command_run_in_the_workspace_does_not_inherit_the_repository_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job's own `git` calls get the same protection as the runner's."""
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere"))
    done = await run_command(["env"], cwd=tmp_path)
    lines = done.stdout.splitlines()
    assert not any(line.startswith(("GIT_DIR=", "GIT_WORK_TREE=")) for line in lines)
    assert f"GIT_CEILING_DIRECTORIES={tmp_path.resolve().parent}" in lines
