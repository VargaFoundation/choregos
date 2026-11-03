# SPDX-License-Identifier: Apache-2.0
"""What the runner writes never enters a commit (S51-08).

`.git/info/exclude` only keeps untracked files out of `git add -A`: a scratch file the branch
already tracks — committed by an older runner, or by a job before this fix — was committed again
with every run that rewrote it. The commit leaves every runner path out, tracked or not.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from choregos_runner.job import FIELDS, JOB_RESULT
from choregos_runner.workspace import RUNNER_FILES, Workspace

pytestmark = pytest.mark.asyncio


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def _init(root: Path, branch: str) -> None:
    _git(root, "init", "-q", "--initial-branch", branch)
    _git(root, "config", "user.name", "seed")
    _git(root, "config", "user.email", "seed@example.org")


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_a_jobs_scratch_files_are_runner_files() -> None:
    assert {FIELDS, JOB_RESULT} <= set(RUNNER_FILES)


async def test_a_scratch_file_the_branch_already_tracks_is_not_committed_again(tmp_path: Path) -> None:
    _init(tmp_path, "main")
    for relative in (".choregos/task.md", ".choregos/job.json", ".claude/skills/review/SKILL.md"):
        _write(tmp_path, relative, "from an older run\n")
    _write(tmp_path, ".choregos/workflow.yaml", "the project's own\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "an older run committed its scratch files")

    workspace = Workspace(tmp_path)
    workspace.exclude([".claude/skills/review/"])
    for relative in (".choregos/task.md", ".choregos/job.json", ".claude/skills/review/SKILL.md"):
        _write(tmp_path, relative, "this run's\n")
    _write(tmp_path, ".choregos/workflow.yaml", "changed by the step\n")
    _write(tmp_path, "src/report.py", "ROWS = 2\n")

    assert await workspace.commit("feat(1): report") is not None
    committed = _git(tmp_path, "show", "--name-only", "--format=", "HEAD").split()
    assert sorted(committed) == [".choregos/workflow.yaml", "src/report.py"], (
        "only the step's own changes are committed; the project's `.choregos/` files are its own"
    )
    assert _git(tmp_path, "show", "HEAD:.choregos/task.md") == "from an older run", "kept as it was"


async def test_without_a_first_commit_the_scratch_files_stay_out_too(tmp_path: Path) -> None:
    """A project without a repository (ADR 0012): a fresh `git init`, nothing tracked yet."""
    _init(tmp_path, "travail")  # lang: fr-ok (the runner's own branch name)
    workspace = Workspace(tmp_path)
    _write(tmp_path, ".choregos/fields.json", "{}\n")
    _write(tmp_path, "notes.md", "a finding\n")

    assert await workspace.commit("docs(1): notes") is not None
    assert _git(tmp_path, "show", "--name-only", "--format=", "HEAD").split() == ["notes.md"]
