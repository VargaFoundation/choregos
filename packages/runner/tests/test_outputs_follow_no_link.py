"""S65-06: nothing in the outputs folder is followed, and neither is the folder.

`collect` refused a symbolic link in place of a file and followed one in place of the folder: the
folder is the agent's to write, and a link there made the runner read, and hand to the platform as
deliverables, files that are the pod's and not the step's.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from choregos_contracts import StageInput
from choregos_runner import outputs
from choregos_runner.outputs import (
    OUTPUTS_DIR,
    NotAFolderError,
    collect,
    open_folder,
    read_regular,
    store_outputs,
)


class Client:
    """What `store_outputs` hands to the internal API."""

    def __init__(self) -> None:
        self.put: dict[str, bytes] = {}

    async def put_output(self, name: str, type_: str, data: bytes, digest: str) -> None:
        self.put[name] = data


def _elsewhere(tmp_path: Path) -> Path:
    """A directory outside the workspace, holding what must never become a deliverable."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "environ").write_bytes(b"TOKEN=not-a-deliverable")
    (elsewhere / "notes.txt").write_bytes(b"not the step's")
    return elsewhere


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


async def test_a_regular_file_of_the_folder_is_stored(stage_input: StageInput, tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    folder = workspace / OUTPUTS_DIR
    folder.mkdir(parents=True)
    (folder / "notes.txt").write_bytes(b"kept")
    client = Client()
    stored, problems = await store_outputs(client, workspace, stage_input)
    assert client.put == {"notes.txt": b"kept"}
    assert stored["notes.txt"].size == 4 and problems == []


@pytest.mark.parametrize("linked", [".choregos/outputs", ".choregos"])
async def test_a_link_in_place_of_the_folder_is_refused_whole(
    stage_input: StageInput, tmp_path: Path, linked: str
) -> None:
    workspace, elsewhere = _workspace(tmp_path), _elsewhere(tmp_path)
    if linked == ".choregos":
        (elsewhere / "outputs").mkdir()
        (elsewhere / "outputs" / "environ").write_bytes(b"TOKEN=not-a-deliverable")
        (workspace / ".choregos").symlink_to(elsewhere, target_is_directory=True)
    else:
        (workspace / ".choregos").mkdir()
        (workspace / OUTPUTS_DIR).symlink_to(elsewhere, target_is_directory=True)
    kept, refused = collect(workspace, stage_input)
    assert kept == []
    assert len(refused) == 1 and "symbolic link" in refused[0] and OUTPUTS_DIR in refused[0]
    client = Client()
    stored, problems = await store_outputs(client, workspace, stage_input)
    assert client.put == {} and stored == {} and problems == refused


async def test_the_pod_s_own_files_are_not_deliverables(stage_input: StageInput, tmp_path: Path) -> None:
    """The case that matters: the folder pointed at the process's own directory."""
    if not Path("/proc/self/environ").exists():
        pytest.skip("no /proc on this platform")
    workspace = _workspace(tmp_path)
    (workspace / ".choregos").mkdir()
    (workspace / OUTPUTS_DIR).symlink_to("/proc/self", target_is_directory=True)
    client = Client()
    stored, problems = await store_outputs(client, workspace, stage_input)
    assert client.put == {} and stored == {}
    assert problems and "symbolic link" in problems[0]


async def test_a_link_in_place_of_a_file_is_still_refused(stage_input: StageInput, tmp_path: Path) -> None:
    workspace, elsewhere = _workspace(tmp_path), _elsewhere(tmp_path)
    folder = workspace / OUTPUTS_DIR
    folder.mkdir(parents=True)
    (folder / "environ").symlink_to(elsewhere / "environ")
    (folder / "notes.txt").write_bytes(b"kept")
    client = Client()
    stored, problems = await store_outputs(client, workspace, stage_input)
    assert client.put == {"notes.txt": b"kept"} and list(stored) == ["notes.txt"]
    assert problems == ["environ: not a regular file"]


async def test_a_file_that_became_a_link_after_it_was_listed_is_not_read(
    stage_input: StageInput, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, elsewhere = _workspace(tmp_path), _elsewhere(tmp_path)
    folder = workspace / OUTPUTS_DIR
    folder.mkdir(parents=True)
    (folder / "notes.txt").write_bytes(b"kept")
    listed = outputs.collect

    def then_swapped(workspace: Path, stage_input: StageInput) -> Any:
        found = listed(workspace, stage_input)
        (folder / "notes.txt").unlink()
        (folder / "notes.txt").symlink_to(elsewhere / "environ")
        return found

    monkeypatch.setattr(outputs, "collect", then_swapped)
    client = Client()
    stored, problems = await store_outputs(client, workspace, stage_input)
    assert client.put == {} and stored == {}
    assert problems == ["notes.txt: not a regular file"]


async def test_a_folder_that_became_a_link_after_it_was_listed_is_not_read(
    stage_input: StageInput, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, elsewhere = _workspace(tmp_path), _elsewhere(tmp_path)
    folder = workspace / OUTPUTS_DIR
    folder.mkdir(parents=True)
    (folder / "notes.txt").write_bytes(b"kept")
    listed = outputs.collect

    def then_swapped(workspace: Path, stage_input: StageInput) -> Any:
        found = listed(workspace, stage_input)
        (folder / "notes.txt").unlink()
        folder.rmdir()
        folder.symlink_to(elsewhere, target_is_directory=True)
        return found

    monkeypatch.setattr(outputs, "collect", then_swapped)
    client = Client()
    stored, problems = await store_outputs(client, workspace, stage_input)
    assert client.put == {} and stored == {}
    assert problems == [f"{OUTPUTS_DIR}: no longer a folder of the workspace, nothing stored"]


def test_what_is_not_a_file_is_refused_without_waiting(stage_input: StageInput, tmp_path: Path) -> None:
    """A FIFO nobody writes to would block a plain open for ever."""
    workspace = _workspace(tmp_path)
    folder = workspace / OUTPUTS_DIR
    folder.mkdir(parents=True)
    os.mkfifo(folder / "pipe")
    (folder / "sub").mkdir()
    kept, refused = collect(workspace, stage_input)
    assert kept == []
    assert refused == ["pipe: not a regular file", "sub: not a regular file"]
    folder_fd = open_folder(workspace)
    assert folder_fd is not None
    try:
        assert read_regular(folder_fd, "pipe") is None
        assert read_regular(folder_fd, "sub") is None
        assert read_regular(folder_fd, "absent") is None
    finally:
        os.close(folder_fd)


def test_no_folder_is_nothing_to_collect(stage_input: StageInput, tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    assert open_folder(workspace) is None
    assert collect(workspace, stage_input) == ([], [])
    (workspace / ".choregos").mkdir()
    assert open_folder(workspace) is None


def test_a_file_in_place_of_the_folder_is_not_a_folder(stage_input: StageInput, tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    (workspace / ".choregos").mkdir()
    (workspace / OUTPUTS_DIR).write_bytes(b"not a folder")
    with pytest.raises(NotAFolderError):
        open_folder(workspace)
    kept, refused = collect(workspace, stage_input)
    assert kept == [] and len(refused) == 1
