# SPDX-License-Identifier: Apache-2.0
"""Binary deliverables: what an agent writes in `.choregos/outputs/` (S31-05, ADR 0049).

An agent that produces a slide deck, a spreadsheet, a document, an image or a video writes it in
`.choregos/outputs/`. The runner hands each file to the internal API, which hashes it, stores it in
the object store and records it as a typed artifact; the runner then lists what was stored in the
result's `artifacts.files` — itself, never the agent.

The platform does not generate these files: an office file comes from a skill run in the sandbox,
an image or a video from a model behind the gateway (whose cost the gateway meters) or from an MCP
connector. The folder is excluded from git: deliverables are artifacts, not commits.

The folder is the agent's to write, so nothing in it is followed (S65-06): `.choregos` and
`.choregos/outputs` are opened one component at a time with `O_NOFOLLOW`, and each file is read
through a descriptor opened the same way, relative to the folder. A symbolic link in place of the
folder, or of a file, is not a deliverable: what it points to is the pod's, not the step's.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Any

from choregos_contracts import (
    ARTIFACT_NAME_PATTERN,
    ARTIFACT_TYPES,
    RESERVED_ARTIFACT_NAMES,
    ArtifactFile,
    StageInput,
    artifact_digest,
    type_of_filename,
)

OUTPUTS_DIR = ".choregos/outputs"
_NAME = re.compile(ARTIFACT_NAME_PATTERN)

#: A component of the folder's path: a directory, and never through a symbolic link.
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
#: A deliverable: never through a symbolic link, and never blocking on what is not a file (a FIFO).
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


class NotAFolderError(Exception):
    """A component of `.choregos/outputs` is a symbolic link, or is not a directory."""


def open_folder(workspace: Path) -> int | None:
    """A descriptor on the outputs folder, opened without following a link at any step.

    None when the folder does not exist; `NotAFolderError` when `.choregos` or `outputs` is a symbolic
    link or a file. The caller closes the descriptor."""
    fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in OUTPUTS_DIR.split("/"):
            try:
                child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                return None
            except OSError as refused:  # ELOOP: a symbolic link; ENOTDIR: a file
                raise NotAFolderError(part) from refused
            os.close(fd)
            fd = child
        opened, fd = fd, -1
        return opened
    finally:
        if fd >= 0:
            os.close(fd)


def read_regular(folder_fd: int, name: str) -> bytes | None:
    """The bytes of a regular file of the folder; None for a link, a folder, a device or a FIFO."""
    try:
        fd = os.open(name, _FILE_FLAGS, dir_fd=folder_fd)
    except OSError:
        return None
    # Judged on the descriptor, before anything wraps it: a directory opens read-only too.
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        return None
    with os.fdopen(fd, "rb") as handle:
        return handle.read()


def _is_regular(folder_fd: int, name: str) -> bool:
    try:
        return stat.S_ISREG(os.stat(name, dir_fd=folder_fd, follow_symlinks=False).st_mode)
    except OSError:
        return False


def expected_files(stage_input: StageInput) -> dict[str, str]:
    """{declared file output: the file name the agent must write}, e.g. `{"deck": "deck.pptx"}`."""
    files: dict[str, str] = {}
    for name in stage_input.transition.file_outputs():
        extension = ARTIFACT_TYPES[stage_input.transition.output_types[name]].extension
        files[name] = f"{name}.{extension}" if extension and not name.endswith(f".{extension}") else name
    return files


def missing_files(outputs_dir: Path, expected: dict[str, str]) -> list[str]:
    return [filename for filename in expected.values() if not (outputs_dir / filename).is_file()]


def collect(workspace: Path, stage_input: StageInput) -> tuple[list[tuple[str, str, Path]], list[str]]:
    """The files to store, as (artifact name, type, path), and the ones refused with why.

    A declared output keeps its declared name and type; any other file is stored under its own file
    name, typed by its extension. Only regular files directly in the folder: a symbolic link could
    point anywhere in the pod, a sub-folder has no artifact name. The folder itself is held to the
    same rule (`open_folder`): a link in its place is refused whole."""
    try:
        folder_fd = open_folder(workspace)
    except NotAFolderError as refused_part:
        return [], [
            f"{OUTPUTS_DIR}: `{refused_part}` is a symbolic link or a file, not a folder of the workspace"
        ]
    if folder_fd is None:
        return [], []
    folder = workspace / OUTPUTS_DIR
    declared = {filename: name for name, filename in expected_files(stage_input).items()}
    kept: list[tuple[str, str, Path]] = []
    refused: list[str] = []
    try:
        names = sorted(os.listdir(folder_fd))
        regular = {name for name in names if _is_regular(folder_fd, name)}
    finally:
        os.close(folder_fd)
    for path in (folder / name for name in names):
        if path.name not in regular:
            refused.append(f"{path.name}: not a regular file")
            continue
        if path.name in declared:
            name = declared[path.name]
            kept.append((name, stage_input.transition.output_types[name], path))
        elif path.name in RESERVED_ARTIFACT_NAMES:
            refused.append(f"{path.name}: this name is measured by the platform")
        elif _NAME.fullmatch(path.name):
            kept.append((path.name, type_of_filename(path.name), path))
        else:
            refused.append(f"{path.name}: not a valid artifact name")
    return kept, refused


async def store_outputs(
    client: Any, workspace: Path, stage_input: StageInput
) -> tuple[dict[str, ArtifactFile], list[str]]:
    """Hands every deliverable to the internal API; returns what it stored and what failed."""
    kept, problems = collect(workspace, stage_input)
    stored: dict[str, ArtifactFile] = {}
    if not kept:
        return stored, problems
    # Opened again, and each file read through it: what was a file when it was listed may be a link
    # by now, and the folder too. Neither is followed.
    try:
        folder_fd = open_folder(workspace)
    except NotAFolderError:
        folder_fd = None
    if folder_fd is None:
        return stored, [*problems, f"{OUTPUTS_DIR}: no longer a folder of the workspace, nothing stored"]
    try:
        for name, type_, path in kept:
            data = read_regular(folder_fd, path.name)
            if data is None:
                problems.append(f"{path.name}: not a regular file")
                continue
            digest = artifact_digest(data)
            try:
                await client.put_output(name, type_, data, digest)
            except Exception as exc:
                problems.append(f"{path.name}: {str(exc)[:200]}")
                continue
            stored[name] = ArtifactFile(
                filename=path.name,
                type=type_,
                media_type=ARTIFACT_TYPES[type_].media_type,
                digest=digest,
                size=len(data),
            )
    finally:
        os.close(folder_fd)
    return stored, problems


def outputs_markdown(stage_input: StageInput) -> str:
    """The section of `.choregos/task.md` that says which files to write, and where."""
    expected = expected_files(stage_input)
    if not expected:
        return ""
    types = stage_input.transition.output_types
    return (
        "\n".join(
            [
                "",
                "## Files to deliver",
                "Write each of these files in `.choregos/outputs/` before you finish; a missing one "
                "fails the step. Produce office files with your skills, images and video with the "
                "models or tools you are given — then write the file there:",
                *[f"- `{OUTPUTS_DIR}/{filename}` ({types[name]})" for name, filename in expected.items()],
            ]
        )
        + "\n"
    )
