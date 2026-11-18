# SPDX-License-Identifier: Apache-2.0
"""Files in the shared tree, opened without following a link (ADR 0146 §6, S65-04b).

The agent writes the tree: it can leave a symbolic link where the runner is about to read or write
(`.choregos/result.json -> /proc/self/environ`, a `.choregos` that is itself a link). A path the runner opens
here is walked one component at a time from the tree's root, each component opened from its parent's
descriptor with `O_NOFOLLOW`, as `hosted_workspace.py` does for the copy it brings back: nothing the agent
planted is followed, and a link swapped in between the check and the open is refused by the open itself.
A refusal is `LinkRefusedError`, which the caller journals; a missing file stays `FileNotFoundError`.

A hard link is not a link to follow: `O_NOFOLLOW` does not see it. A file the agent made a second name for
(`ln other .choregos/task.md`) is the same inode as `other`, so a runner that truncates it empties `other`,
and one that reads it reads `other`. A file with more than one name is therefore refused, for a write and for
a read, before anything is truncated or returned (S65-04b, hard links). `docs/security.md` says what this
does and does not cover.
"""

from __future__ import annotations

import errno
import os
import shutil
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
#: No `O_TRUNC`: the file is truncated once its link count has been read, never before.
_WRITE = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC


class LinkRefusedError(OSError):
    """The path crosses a symbolic link, or is not a regular file: the runner opens neither."""

    def __init__(self, path: Path | str, why: str = "is a link") -> None:
        super().__init__(errno.ELOOP, f"`{path}` {why}: the runner does not follow it")
        self.path = str(path)


def _check_regular(info: os.stat_result, path: Path | str) -> None:
    if not stat.S_ISREG(info.st_mode):
        raise LinkRefusedError(path, "is not a regular file")
    if info.st_nlink > 1:
        raise LinkRefusedError(path, "has several hard links")


def _parts(root: Path, path: Path | str) -> list[str]:
    candidate = Path(path)
    relative = candidate.relative_to(root) if candidate.is_absolute() else candidate
    parts = [p for p in relative.parts if p not in ("", ".")]
    if not parts or ".." in parts:
        raise LinkRefusedError(path, "is not below the workspace")
    return parts


def _refuse(exc: OSError, path: Path | str) -> OSError:
    return LinkRefusedError(path) if exc.errno in (errno.ELOOP, errno.ENOTDIR) else exc


@contextmanager
def _parent(root: Path, parts: list[str], *, create: bool, shown: Path | str) -> Iterator[int]:
    """The descriptor of the directory holding the last component, every directory opened `O_NOFOLLOW`."""
    if create:
        Path(root).mkdir(parents=True, exist_ok=True)
    fd = os.open(root, _DIR)
    try:
        for name in parts[:-1]:
            try:
                nxt = os.open(name, _DIR, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(name, 0o775, dir_fd=fd)
                nxt = os.open(name, _DIR, dir_fd=fd)
            except OSError as exc:
                raise _refuse(exc, shown) from exc
            os.close(fd)
            fd = nxt
        yield fd
    finally:
        os.close(fd)


def read_bytes(root: Path, path: Path | str, *, limit: int | None = None) -> bytes:
    """The content of the regular file at `path` below `root`."""
    parts = _parts(root, path)
    with _parent(root, parts, create=False, shown=path) as parent:
        try:
            fd = os.open(parts[-1], _READ, dir_fd=parent)
        except OSError as exc:
            raise _refuse(exc, path) from exc
        with os.fdopen(fd, "rb") as handle:
            _check_regular(os.fstat(handle.fileno()), path)
            return handle.read() if limit is None else handle.read(limit)


def read_text(root: Path, path: Path | str) -> str:
    return read_bytes(root, path).decode("utf-8")


def write_bytes(
    root: Path, path: Path | str, data: bytes, *, append: bool = False, mode: int = 0o664
) -> None:
    """Writes the regular file at `path` below `root`, its directories created on the way. A file that has
    another name is refused before it is truncated."""
    parts = _parts(root, path)
    with _parent(root, parts, create=True, shown=path) as parent:
        try:
            fd = os.open(parts[-1], _WRITE | (os.O_APPEND if append else 0), mode, dir_fd=parent)
        except OSError as exc:
            raise _refuse(exc, path) from exc
        with os.fdopen(fd, "ab" if append else "wb") as handle:
            _check_regular(os.fstat(handle.fileno()), path)
            if not append:
                handle.truncate(0)
            handle.write(data)


def write_text(root: Path, path: Path | str, text: str, *, append: bool = False, mode: int = 0o664) -> None:
    write_bytes(root, path, text.encode("utf-8"), append=append, mode=mode)


def remove(root: Path, path: Path | str) -> None:
    """Removes what stands at `path` below `root` (a link is unlinked, a directory emptied from its own
    descriptor), following nothing. Nothing there is not an error."""
    parts = _parts(root, path)
    try:
        with _parent(root, parts, create=False, shown=path) as parent:
            try:
                info = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return
            if stat.S_ISDIR(info.st_mode):
                shutil.rmtree(parts[-1], dir_fd=parent)  # descriptor-based on Linux: follows no link
            else:
                os.unlink(parts[-1], dir_fd=parent)
    except FileNotFoundError:
        return


def chmod(root: Path, path: Path | str, mode: int) -> None:
    """Sets the mode of the regular file at `path` below `root`, through its descriptor."""
    parts = _parts(root, path)
    with _parent(root, parts, create=False, shown=path) as parent:
        try:
            fd = os.open(parts[-1], _READ, dir_fd=parent)
        except OSError as exc:
            raise _refuse(exc, path) from exc
        try:
            _check_regular(os.fstat(fd), path)
            os.fchmod(fd, mode)
        finally:
            os.close(fd)


def is_regular(root: Path, path: Path | str) -> bool:
    """Whether `path` below `root` is a regular file reached through no link."""
    try:
        read_bytes(root, path, limit=0)
    except OSError:
        return False
    return True


def regular_files(root: Path, folder: Path | str) -> tuple[list[str], list[str]]:
    """The names directly in `folder`, split into regular files and what the runner refuses (a link, a
    directory, anything else). A missing folder, or one that is a link, has none of either — the second
    is reported as refused by `LinkRefusedError`."""
    parts = _parts(root, folder)
    with _parent(root, parts, create=False, shown=folder) as holder:
        try:
            fd = os.open(parts[-1], _DIR, dir_fd=holder)
        except OSError as exc:
            raise _refuse(exc, folder) from exc
    try:
        names = sorted(os.listdir(fd))
        kept: list[str] = []
        refused: list[str] = []
        for name in names:
            mode = os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode
            (kept if stat.S_ISREG(mode) else refused).append(name)
        return kept, refused
    finally:
        os.close(fd)
