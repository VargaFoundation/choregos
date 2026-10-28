# SPDX-License-Identifier: Apache-2.0
"""Paths a document names inside its own directory: never outside, never through a symbolic link.

Shared by the templates (`choregos_api.services.gabarits`) and the plugins (`choregos_core.plugins`,
ADR 0093): a manifest names files relative to its directory, and nothing it names may be read from
elsewhere.
"""

from __future__ import annotations

import os
from pathlib import Path


class PathOutsideError(ValueError):
    """A path that resolves outside the directory that names it."""


class SymlinkRefusedError(ValueError):
    """A symbolic link inside a directory whose files are read."""


def inside(root: Path, ref: str) -> Path:
    """`root / ref`, resolved; `PathOutsideError` when it leaves `root` (`..`, an absolute path, a link)."""
    base = root.resolve()
    path = (base / ref).resolve()
    if not path.is_relative_to(base):
        raise PathOutsideError(f"`{ref}` goes outside the folder")
    return path


def symlinks_under(root: Path) -> list[str]:
    """Every symbolic link under `root`, as paths relative to it, sorted; links are not followed."""
    found: list[str] = []
    for current, dirs, files in os.walk(root, followlinks=False):
        here = Path(current)
        for name in [*dirs, *files]:
            if (here / name).is_symlink():
                found.append((here / name).relative_to(root).as_posix())
    return sorted(found)


def files_under(root: Path) -> dict[str, Path]:
    """The regular files under `root`, relative POSIX path → path, sorted; `SymlinkRefusedError` on a link."""
    links = symlinks_under(root)
    if links:
        raise SymlinkRefusedError(f"symbolic link refused ({links[0]})")
    return {path.relative_to(root).as_posix(): path for path in sorted(root.rglob("*")) if path.is_file()}


__all__ = ["PathOutsideError", "SymlinkRefusedError", "files_under", "inside", "symlinks_under"]
