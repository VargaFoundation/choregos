# SPDX-License-Identifier: Apache-2.0
"""The working tree a hosted container works in (S41-18, ADR 0080 §7).

The container never sees the runner's checkout: its `.git` holds the history the runner pushes
with, and the runner's volume is the runner's alone. Before the step, `export_tree` copies the
working tree, without `.git` and the runner's own files, into a volume the two containers share;
the container sees it at `/workspace`, read-only for `workspace: read`. After a `write` step,
`import_tree` brings back what changed, one file at a time, each judged as an ACP agent's write
is (`GuardRails.check_write`): what the guard refuses, a `.git` path, a symlink out of the tree or
anything but a file stays in the shared volume and is journaled as `hosted.write_refused`.

The container may still be running while the runner copies: every path is opened from its parent's
descriptor with `O_NOFOLLOW`, so a symlink it swaps in mid-copy (`src -> /proc/self`) is a refused
entry, never a path the runner follows into its own files or its environment.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import posixpath
import shutil
import stat
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from choregos_core.hosted import SHARED_WORKSPACE_AGENT

from .guardrails import Decision, GuardRails
from .workspace import Workspace

#: What the runner writes for itself: never exported, never brought back.
NOT_EXPORTED = frozenset(
    {".choregos/transcript.jsonl", ".choregos/result.json", ".choregos/acp-session.json"}
)
#: The one place under `.choregos/` a hosted container's files come back from: its deliverables.
OUTPUTS = ".choregos/outputs/"
#: Group-writable, in the group the pod's `fsGroup` (and `--group-add` in docker) gives both
#: containers: the container changes the files the runner copied. Directories keep the group.
DIR_MODE = 0o2775
FILE_MODE = 0o664
_EXEC = 0o111
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_CHUNK = 1 << 20


class HostedWorkspaceError(RuntimeError):
    """The shared working tree could not be laid out."""


@dataclass(frozen=True, slots=True)
class Refusal:
    path: str
    reason: str


@dataclass(slots=True)
class Imported:
    written: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    refused: list[Refusal] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Entry:
    """One entry of the shared tree, read through descriptors: a file's digest and mode, or a
    symlink's target; `refused` says why it cannot come back."""

    kind: str  # file | symlink | other
    digest: str = ""
    executable: bool = False
    target: str = ""
    refused: str = ""


# ───────────────────────── export ─────────────────────────


def export_tree(workspace: Path, shared: Path) -> int:
    """Empties `shared` and copies the working tree into it, `.git` and the runner's files left
    out; the number of files and symlinks copied."""
    root = os.open(shared, _DIR_FLAGS)
    try:
        for name in os.listdir(root):
            _remove(root, name)
        return _copy_dir(workspace, "", root)
    finally:
        os.close(root)


def _copy_dir(source: Path, rel: str, dir_fd: int) -> int:
    copied = 0
    for entry in sorted(os.scandir(source / rel if rel else source), key=lambda e: e.name):
        path = f"{rel}{entry.name}"
        if entry.name == ".git" or path in NOT_EXPORTED:
            continue
        info = entry.stat(follow_symlinks=False)
        try:
            if stat.S_ISDIR(info.st_mode):
                os.mkdir(entry.name, DIR_MODE, dir_fd=dir_fd)
                child = os.open(entry.name, _DIR_FLAGS, dir_fd=dir_fd)
                try:
                    os.fchmod(child, DIR_MODE)
                    copied += _copy_dir(source, f"{path}/", child)
                finally:
                    os.close(child)
            elif stat.S_ISLNK(info.st_mode):
                os.symlink(os.readlink(entry.path), entry.name, dir_fd=dir_fd)
                copied += 1
            elif stat.S_ISREG(info.st_mode):
                _copy_file(entry.path, entry.name, dir_fd, info.st_mode & _EXEC)
                copied += 1
        except OSError as exc:  # the container raced the copy: its tree, not the runner's
            raise HostedWorkspaceError(f"`{path}` could not be copied: {exc}") from exc
    return copied


def _copy_file(source: str, name: str, dir_fd: int, executable: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    out = os.open(name, flags, FILE_MODE, dir_fd=dir_fd)
    try:
        with open(source, "rb") as src:
            while chunk := src.read(_CHUNK):
                os.write(out, chunk)
        os.fchmod(out, FILE_MODE | (0o110 if executable else 0))
    finally:
        os.close(out)


def _remove(dir_fd: int, name: str) -> None:
    info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    if stat.S_ISDIR(info.st_mode):
        shutil.rmtree(name, dir_fd=dir_fd)  # descriptor-based on Linux: it follows no symlink
    else:
        os.unlink(name, dir_fd=dir_fd)


# ───────────────────────── import ─────────────────────────


async def import_tree(shared: Path, workspace: Workspace, guards: GuardRails) -> Imported:
    """Brings back into the checkout what the container changed in `shared`: written, deleted and
    refused paths. Each refusal joins the guard rails' decisions, so it counts as a denial."""
    entries: dict[str, _Entry] = {}
    root = os.open(shared, _DIR_FLAGS)
    try:
        _read_tree(root, "", entries)
    finally:
        os.close(root)
    done = Imported()
    candidates = sorted(path for path, entry in entries.items() if _differs(workspace.path / path, entry))
    gone = sorted(set(_tracked_tree(workspace.path)) - set(entries))
    # Deletions first: a file the container turned into a directory (or the reverse) is a deletion
    # and a write, and the write needs the place free. No project command runs before the copy back,
    # so the checkout holds nothing ignored a deletion could concern.
    for path in gone:
        if why := _refused_destination(workspace.path, path, guards):
            _refuse(done, guards, path, why)
            continue
        (workspace.path / path).unlink()
        _prune_empty_parents(workspace.path, path)
        done.deleted.append(path)
    ignored = await _ignored(workspace, candidates)
    for path in candidates:
        if path in ignored:
            continue
        entry = entries[path]
        if why := entry.refused or _refused_destination(workspace.path, path, guards):
            _refuse(done, guards, path, why)
            continue
        _write(shared, workspace.path, path, entry)
        done.written.append(path)
    return done


def _prune_empty_parents(workspace: Path, path: str) -> None:
    parent = (workspace / path).parent
    while parent != workspace and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent


def _read_tree(dir_fd: int, rel: str, entries: dict[str, _Entry]) -> None:
    for name in sorted(os.listdir(dir_fd)):
        path = f"{rel}{name}"
        if name == ".git":
            entries[path] = _Entry("other", refused="a `.git` path never comes back to the repository")
            continue
        try:
            info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if stat.S_ISDIR(info.st_mode):
            try:
                child = os.open(name, _DIR_FLAGS, dir_fd=dir_fd)
            except OSError:  # swapped for something else since the `stat`
                entries[path] = _Entry("other", refused="changed while it was read")
                continue
            try:
                _read_tree(child, f"{path}/", entries)
            finally:
                os.close(child)
        elif stat.S_ISLNK(info.st_mode):
            target = os.readlink(name, dir_fd=dir_fd)
            entries[path] = _Entry("symlink", target=target, refused=_escaping_link(path, target))
        elif stat.S_ISREG(info.st_mode):
            entries[path] = _read_file(dir_fd, name)
        else:
            entries[path] = _Entry("other", refused="not a regular file")
    for path, entry in list(entries.items()):
        if path.startswith(".choregos/") and not entry.refused and not _an_output(path, entry):
            entries[path] = _Entry(
                entry.kind,
                entry.digest,
                entry.executable,
                entry.target,
                refused="under `.choregos/`, only the files of `.choregos/outputs/` come back",
            )


def _read_file(dir_fd: int, name: str) -> _Entry:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dir_fd)
    except OSError:
        return _Entry("other", refused="changed while it was read")
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return _Entry("other", refused="not a regular file")
        digest = hashlib.sha256()
        while chunk := os.read(fd, _CHUNK):
            digest.update(chunk)
        return _Entry("file", digest=digest.hexdigest(), executable=bool(info.st_mode & 0o100))
    finally:
        os.close(fd)


def _an_output(path: str, entry: _Entry) -> bool:
    return path.startswith(OUTPUTS) and entry.kind == "file"


def _escaping_link(path: str, target: str) -> str:
    if posixpath.isabs(target):
        return f"a symlink to an absolute path (`{target}`)"
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
    if resolved == ".." or resolved.startswith("../"):
        return f"a symlink out of the repository (`{target}`)"
    return ""


def _differs(target: Path, entry: _Entry) -> bool:
    try:
        info = target.lstat()
    except FileNotFoundError:
        return True
    if entry.kind == "symlink":
        return not stat.S_ISLNK(info.st_mode) or os.readlink(target) != entry.target
    if entry.kind == "file" and stat.S_ISREG(info.st_mode):
        return _digest(target) != entry.digest or bool(info.st_mode & 0o100) != entry.executable
    return True


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as src:
        while chunk := src.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _refused_destination(workspace: Path, path: str, guards: GuardRails) -> str:
    """Why `path` cannot be written or deleted in the checkout, or an empty string."""
    target = workspace / path
    # Through a symlink the checkout already has, the write would land elsewhere than the path the
    # guard judges (`src/up -> ..`): the path must be the one it names.
    if target.parent.resolve() != workspace.resolve() / Path(path).parent:
        return "the path goes through a symlink of the repository"
    if target.is_dir() and not target.is_symlink():
        return "a directory of the repository cannot become a file"
    decision = guards.check_write(path)
    return "" if decision.allowed else decision.reason


def _write(shared: Path, workspace: Path, path: str, entry: _Entry) -> None:
    target = workspace / path
    target.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(FileNotFoundError):
        target.unlink()
    if entry.kind == "symlink":
        target.symlink_to(entry.target)
        return
    parts = path.split("/")
    fd = os.open(shared, _DIR_FLAGS)
    try:
        for part in parts[:-1]:
            child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        src = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
    finally:
        os.close(fd)
    try:
        if not stat.S_ISREG(os.fstat(src).st_mode):
            raise HostedWorkspaceError(f"`{path}` changed while it was copied back")
        with target.open("wb") as out:
            while chunk := os.read(src, _CHUNK):
                out.write(chunk)
    finally:
        os.close(src)
    target.chmod(0o755 if entry.executable else 0o644)


def _tracked_tree(workspace: Path) -> Iterator[str]:
    """The checkout's files and symlinks a deletion may concern: not `.git`, not `.choregos/`."""
    for current, dirs, files in os.walk(workspace):
        rel = Path(current).relative_to(workspace)
        dirs[:] = [d for d in dirs if d != ".git" and not (rel == Path(".") and d == ".choregos")]
        for name in files:
            yield str(rel / name) if rel != Path(".") else name
        for name in dirs:
            if (Path(current) / name).is_symlink():
                yield str(rel / name) if rel != Path(".") else name


async def _ignored(workspace: Workspace, paths: list[str]) -> set[str]:
    """The paths the checkout ignores (`.gitignore`, the runner's excludes): they are never
    committed, so they are neither judged nor copied — `__pycache__/` is not a refused write.
    The step's deliverables are ignored too and still come back."""
    candidates = [path for path in paths if not path.startswith(".choregos/")]
    ignored: set[str] = set()
    for start in range(0, len(candidates), 500):
        found = await workspace.git("check-ignore", "--", *candidates[start : start + 500])
        ignored.update(line for line in found.stdout.splitlines() if line)
    return ignored


def _refuse(done: Imported, guards: GuardRails, path: str, reason: str) -> None:
    done.refused.append(Refusal(path, reason))
    guards.decisions.append(Decision(False, reason, "write", f"{SHARED_WORKSPACE_AGENT}/{path}"))
