"""A project without a repository: the agent works in an empty directory (ADR 0012, limit no. 1).

A trade that examines case files, qualifies profiles or drafts letters has no
repository. It still had to declare one, which served no purpose, and the runner cloned
a fake workspace to promise a branch that nobody would read.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from choregos_contracts import StageInput
from choregos_runner.workspace import Workspace

pytestmark = pytest.mark.asyncio


@pytest.fixture
def no_repository(stage_input: StageInput) -> StageInput:
    return stage_input.model_copy(update={"repo": None})


async def test_the_workspace_is_an_empty_versioned_directory(
    no_repository: StageInput, tmp_path: Path
) -> None:
    """Empty, but tracked by git: this is what lets the runner measure what the agent
    wrote, without cloning or pushing anything."""
    workspace = Workspace(tmp_path / "work")
    await workspace.prepare(no_repository)

    assert (tmp_path / "work" / ".git").is_dir(), "local git, to measure the work"
    files = [p.name for p in (tmp_path / "work").iterdir() if p.name != ".git"]
    assert files == [], f"the directory should be empty, it holds {files}"

    remotes = await workspace.git("remote")
    assert remotes.stdout.strip() == "", "no remote: there is nowhere to push"


async def test_what_the_agent_writes_is_seen_as_added(no_repository: StageInput, tmp_path: Path) -> None:
    """The reference is git's empty tree: everything is "added", which is the truth
    for a directory that had nothing."""
    from choregos_runner.runner import EMPTY_TREE

    workspace = Workspace(tmp_path / "work")
    await workspace.prepare(no_repository)
    (tmp_path / "work" / "case-file.md").write_text("# Case file examined\n", encoding="utf-8")
    await workspace.git("add", "-A")
    await workspace.commit("docs: examine the case file")

    changes = await workspace.changed_files(EMPTY_TREE)
    assert changes == ["case-file.md"], changes
    additions, deletions = await workspace.diff_stats(EMPTY_TREE)
    assert additions == 1 and deletions == 0


async def test_the_contract_accepts_a_step_without_a_repository(no_repository: StageInput) -> None:
    """And the contract says so: `repo` is absent, not filled with a fake repository."""
    dump = no_repository.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert "repo" not in dump
    assert StageInput.model_validate(dump).repo is None
