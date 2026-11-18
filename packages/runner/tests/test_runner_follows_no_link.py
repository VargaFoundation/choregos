"""S65-04b — the runner follows no link in the tree the agent writes (ADR 0146 §6).

The agent owns the tree. Where the runner reads or writes, a symbolic link the agent left would carry it
to a file of its own (`/proc/self/environ`, the service-account token, another run's files). Each reader
and writer below meets a link in the place it works, and refuses it: the secret behind the link never
reaches a result, an artifact, a prompt or the journal, and the refusal is said.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from tranibor_runner import safe_files
from tranibor_runner.outputs import collect
from tranibor_runner.remote import _input_parts, _message_text
from tranibor_runner.result import load_result, write_result
from tranibor_runner.runner import SAVED_SESSION, EventJournal, _events_already_journaled, _saved_session

from .conftest import valid_result

SECRET = "SECRET-BEHIND-THE-LINK"


@pytest.fixture
def tree(tmp_path: Path) -> tuple[Path, Path]:
    """(workspace, the secret file outside it)."""
    workspace = tmp_path / "workspace"
    (workspace / ".choregos").mkdir(parents=True)
    secret = tmp_path / "secret.txt"
    secret.write_text(SECRET, encoding="utf-8")
    return workspace, secret


def _link(at: Path, to: Path) -> None:
    at.parent.mkdir(parents=True, exist_ok=True)
    at.symlink_to(to)


def test_a_result_that_is_a_link_is_refused_and_its_target_never_read(tree: tuple[Path, Path]) -> None:
    workspace, secret = tree
    secret.write_text(json.dumps(valid_result()), encoding="utf-8")  # a result the contract would accept
    _link(workspace / ".choregos" / "result.json", secret)
    load = load_result(workspace / ".choregos" / "result.json")
    assert load.result is None
    assert "link" in (load.error or "")


def test_a_choregos_folder_that_is_a_link_is_refused(tmp_path: Path, tree: tuple[Path, Path]) -> None:
    workspace, _ = tree
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "result.json").write_text(json.dumps(valid_result()), encoding="utf-8")
    (workspace / ".choregos").rmdir()
    _link(workspace / ".choregos", elsewhere)
    assert load_result(workspace / ".choregos" / "result.json").result is None
    with pytest.raises(safe_files.LinkRefusedError):
        write_result(
            workspace / ".choregos" / "result.json", load_result(elsewhere / "result.json").result or _stub()
        )
    assert (
        not (elsewhere / "result.json").read_text().startswith("{\n")
    )  # nothing was written through the link


def _stub() -> Any:
    from tranibor_contracts import StageResult

    return StageResult.model_validate({**valid_result(), "schema": "choregos/StageResult/v1"})


def test_a_result_written_over_a_link_leaves_the_target_alone(tree: tuple[Path, Path]) -> None:
    workspace, secret = tree
    _link(workspace / ".choregos" / "result.json", secret)
    with pytest.raises(safe_files.LinkRefusedError):
        write_result(workspace / ".choregos" / "result.json", _stub())
    assert secret.read_text(encoding="utf-8") == SECRET


def test_a_deliverable_that_is_a_link_is_not_collected(tree: tuple[Path, Path], stage_input: Any) -> None:
    workspace, secret = tree
    _link(workspace / ".choregos" / "outputs" / "notes.md", secret)
    kept, refused = collect(workspace, stage_input)
    assert kept == []
    assert any("notes.md" in why for why in refused)


def test_an_outputs_folder_that_is_a_link_collects_nothing(
    tmp_path: Path, tree: tuple[Path, Path], stage_input: Any
) -> None:
    workspace, _ = tree
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "notes.md").write_text(SECRET, encoding="utf-8")
    _link(workspace / ".choregos" / "outputs", elsewhere)
    kept, refused = collect(workspace, stage_input)
    assert kept == []
    assert refused, "the refusal is said"


def test_an_input_that_is_a_link_is_not_sent_to_the_agent(tree: tuple[Path, Path]) -> None:
    workspace, secret = tree
    _link(workspace / ".choregos" / "inputs" / "brief.txt", secret)
    assert _input_parts(workspace) == []


def test_a_task_file_that_is_a_link_does_not_reach_the_prompt(
    tree: tuple[Path, Path], stage_input: Any
) -> None:
    workspace, secret = tree
    _link(workspace / ".choregos" / "task.md", secret)
    assert SECRET not in _message_text(stage_input, workspace)


def test_a_saved_session_that_is_a_link_is_not_read(tree: tuple[Path, Path]) -> None:
    workspace, secret = tree
    secret.write_text(json.dumps({"backend": "codex", "session_id": "stolen"}), encoding="utf-8")
    _link(workspace / SAVED_SESSION, secret)
    assert _saved_session(workspace) is None


def test_a_journal_that_is_a_link_is_not_followed_in_either_direction(tree: tuple[Path, Path]) -> None:
    workspace, secret = tree
    path = workspace / ".choregos" / "transcript.jsonl"
    _link(path, secret)
    assert _events_already_journaled(path) == 0
    journal = EventJournal(None, path)
    asyncio.run(journal.record("x", {"a": 1}))  # a refused log does not fail the run
    asyncio.run(journal.record("y", {"a": 2}))
    assert secret.read_text(encoding="utf-8") == SECRET
    # ... the API still gets every event, and the refusal is said once, in the journal.
    assert [e["type"] for e in journal.buffer] == ["x", "journal.local_copy_refused", "y"]
    assert [e["seq"] for e in journal.buffer] == [1, 2, 3]


def test_a_link_swapped_in_after_the_check_is_refused_by_the_open_itself(tree: tuple[Path, Path]) -> None:
    workspace, secret = tree
    target = workspace / ".choregos" / "result.json"
    target.write_text("{}", encoding="utf-8")
    assert safe_files.is_regular(workspace, Path(".choregos/result.json"))
    target.unlink()
    _link(target, secret)
    with pytest.raises(safe_files.LinkRefusedError):
        safe_files.read_text(workspace, Path(".choregos/result.json"))


def test_a_fifo_is_not_read(tree: tuple[Path, Path]) -> None:
    import os

    workspace, _ = tree
    os.mkfifo(workspace / ".choregos" / "task.md")
    with pytest.raises(safe_files.LinkRefusedError):
        safe_files.read_bytes(workspace, Path(".choregos/task.md"))
