# SPDX-License-Identifier: Apache-2.0
"""A git repository's documentation (S33-04): https only, a tracked branch or a pinned commit.

One shallow fetch of the ref into a temporary directory; the files matching `paths` (by default
markdown anywhere) become documents, versioned by their blob id. The cursor is the commit: the
same commit is no change; another is a full listing, and a file gone from it was deleted.
"""

from __future__ import annotations

import asyncio
import fnmatch
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .base import Changes, SourceItem, SourceRefusedError

DEFAULT_PATHS = ("*.md", "**/*.md")
MAX_FILES = 500
MAX_FILE_BYTES = 5 * 1024 * 1024
TIMEOUT_S = 300.0


class GitSource:
    def __init__(
        self, *, url: str, ref: str = "main", paths: list[str] | None = None, allow_file: bool = False
    ) -> None:
        scheme = urlsplit(url).scheme
        if scheme != "https" and not (allow_file and scheme == "file"):
            raise SourceRefusedError(f"a git source is fetched over https: `{url}`")
        if ref.startswith("-") or not ref.strip():
            raise SourceRefusedError(f"not a ref: `{ref}`")
        self.url = url
        self.ref = ref
        self.paths = tuple(paths or DEFAULT_PATHS)
        self._checkout: Path | None = None

    async def _git(self, cwd: Path, *args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"PATH": os.environ.get("PATH", ""), "GIT_TERMINAL_PROMPT": "0", "HOME": str(cwd)},
        )
        try:
            out, err = await asyncio.wait_for(process.communicate(), TIMEOUT_S)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        if process.returncode != 0:
            raise RuntimeError(f"git {args[0]}: {err.decode('utf-8', 'replace').strip()[:300]}")
        return out.decode("utf-8", "replace")

    def _wanted(self, path: str) -> bool:
        return any(fnmatch.fnmatch(path, pattern) for pattern in self.paths)

    async def changes(self, cursor: dict[str, Any] | None) -> Changes:
        self.close()
        self._checkout = Path(tempfile.mkdtemp(prefix="choregos-kb-git-"))
        await self._git(self._checkout, "init", "-q")
        await self._git(self._checkout, "fetch", "-q", "--depth", "1", "--", self.url, self.ref)
        commit = (await self._git(self._checkout, "rev-parse", "FETCH_HEAD")).strip()
        if (cursor or {}).get("commit") == commit:
            return Changes(cursor={"commit": commit})
        await self._git(self._checkout, "checkout", "-q", "FETCH_HEAD")
        listing = await self._git(self._checkout, "ls-tree", "-r", "-l", "FETCH_HEAD")
        items: list[SourceItem] = []
        for line in listing.splitlines():
            meta, path = line.split("\t", 1)
            _, kind, blob, size = meta.split()
            if kind != "blob" or not self._wanted(path) or size == "-" or int(size) > MAX_FILE_BYTES:
                continue
            items.append(SourceItem(ref=path, name=Path(path).name, version=blob, url=None, size=int(size)))
            if len(items) >= MAX_FILES:
                break
        return Changes(items=items, full=True, cursor={"commit": commit})

    async def fetch(self, item: SourceItem) -> bytes:
        if self._checkout is None:
            raise RuntimeError("fetch before changes")
        target = (self._checkout / item.ref).resolve()
        if not target.is_relative_to(self._checkout.resolve()):
            raise SourceRefusedError(f"`{item.ref}` leaves the repository")
        return target.read_bytes()

    def close(self) -> None:
        if self._checkout is not None:
            shutil.rmtree(self._checkout, ignore_errors=True)
            self._checkout = None
