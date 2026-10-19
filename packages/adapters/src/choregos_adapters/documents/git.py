# SPDX-License-Identifier: Apache-2.0
"""A git repository's documentation (S33-04): https only, a tracked branch or a pinned commit.

One shallow fetch of the ref into a temporary directory; the files matching `paths` (by default
markdown anywhere) become documents, versioned by their blob id. The cursor is the commit: the
same commit is no change; another is a full listing, and a file gone from it was deleted.

A private repository is read with a credential (a token, or a GitHub App's short-lived installation
token) that goes to git through `GIT_CONFIG_*` environment variables as an `http.extraHeader`: never
in argv (`ps`), in an error message or in `.git/config`. The credential is pinned to its host: a URL
on another host is refused, so a token cannot be sent elsewhere.
"""

from __future__ import annotations

import asyncio
import base64
import fnmatch
import os
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from choregos_core.egress import check_current

from .base import Changes, SourceItem, SourceRefusedError

DEFAULT_PATHS = ("*.md", "**/*.md")
MAX_FILES = 500
MAX_FILE_BYTES = 5 * 1024 * 1024
TIMEOUT_S = 300.0
DEFAULT_HOST = "github.com"

#: A credential is a token, or a coroutine that mints one when the source is read (a GitHub App's).
Credential = str | Callable[[], Awaitable[str]]


class GitSource:
    def __init__(
        self,
        *,
        url: str,
        ref: str = "main",
        paths: list[str] | None = None,
        allow_file: bool = False,
        credential: Credential | None = None,
        host: str | None = None,
        user_agent: str = "",
    ) -> None:
        parts = urlsplit(url)
        scheme = parts.scheme
        if scheme != "https" and not (allow_file and scheme == "file" and credential is None):
            raise SourceRefusedError(f"a git source is fetched over https: `{url}`")
        self.host = (host or DEFAULT_HOST).lower()
        if credential is not None and (parts.hostname or "").lower() != self.host:
            raise SourceRefusedError(
                f"`{url}` is not on `{self.host}`: the connector's credential is never sent to another host"
            )
        if parts.username or parts.password:
            raise SourceRefusedError("a git URL carries no credentials: use a `documents/git` connector")
        self._credential = credential
        if ref.startswith("-") or not ref.strip():
            raise SourceRefusedError(f"not a ref: `{ref}`")
        self.url = url
        self.ref = ref
        self.paths = tuple(paths or DEFAULT_PATHS)
        #: Sent as the HTTP User-Agent of the fetch when given (ADR 0070).
        self.user_agent = user_agent
        self._checkout: Path | None = None

    def _env(self, cwd: Path, token: str | None) -> dict[str, str]:
        env = {"PATH": os.environ.get("PATH", ""), "GIT_TERMINAL_PROMPT": "0", "HOME": str(cwd)}
        if token:
            basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            env |= {
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": f"http.https://{self.host}/.extraHeader",
                "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
            }
        return env

    async def _git(self, cwd: Path, *args: str, token: str | None = None) -> str:
        process = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env(cwd, token),
        )
        try:
            out, err = await asyncio.wait_for(process.communicate(), TIMEOUT_S)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        if process.returncode != 0:
            message = err.decode("utf-8", "replace").strip()
            if token:
                message = message.replace(token, "***")
            raise RuntimeError(f"git {args[0]}: {message[:300]}")
        return out.decode("utf-8", "replace")

    async def _token(self) -> str | None:
        if self._credential is None or isinstance(self._credential, str):
            return self._credential
        return await self._credential()

    def _wanted(self, path: str) -> bool:
        return any(fnmatch.fnmatch(path, pattern) for pattern in self.paths)

    async def changes(self, cursor: dict[str, Any] | None) -> Changes:
        self.close()
        self._checkout = Path(tempfile.mkdtemp(prefix="choregos-kb-git-"))
        await self._git(self._checkout, "init", "-q")
        token = await self._token()
        # A subprocess cannot go through the guarded transport: its host is checked up front, and a
        # redirect (which would reach another host) is refused (S39-22).
        parts = urlsplit(self.url)
        if parts.scheme == "https" and parts.hostname:
            check_current(parts.hostname, parts.port or 443)
        agent = ["-c", f"http.userAgent={self.user_agent}"] if self.user_agent else []
        await self._git(
            self._checkout,
            "-c",
            "http.followRedirects=false",
            *agent,
            "fetch",
            "-q",
            "--depth",
            "1",
            "--",
            self.url,
            self.ref,
            token=token,
        )
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
            # Named by the whole path: two `README.md` in two folders are two documents.
            name = path.replace("/", "-")
            items.append(SourceItem(ref=path, name=name, version=blob, url=None, size=int(size)))
            if len(items) >= MAX_FILES:
                break
        if not items:
            # No file matched: a wrong `paths`, a renamed branch, an emptied repository. Not a deletion.
            return Changes(items=[], full=True, cursor={"commit": commit}, unreadable=[self.url])
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


class GitDocumentsConnector:
    """A `documents/git` connector: the credentials of a git host, opened per repository.

    `auth: token` reads a token (a fine-grained personal access token with `Contents: read`);
    `auth: github_app` mints, at each read, a one-hour installation token of the GitHub App
    (`app_id`, `private_key`, optional `installation_id`) scoped to that repository with
    `contents: read`: no long-lived personal token.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.host = str(config.get("host") or DEFAULT_HOST)
        self.auth = str(config.get("auth") or "token")

    def _credential(self, url: str) -> Credential:
        if self.auth == "token":
            token = str(self.config.get("token") or "")
            if not token:
                raise SourceRefusedError("the `documents/git` connector has no `token`")
            return token
        if self.auth != "github_app":
            raise SourceRefusedError(f"unknown git auth `{self.auth}`: `token` or `github_app`")
        app_id, key = str(self.config.get("app_id") or ""), str(self.config.get("private_key") or "")
        if not (app_id and key):
            raise SourceRefusedError("a `github_app` connector has an `app_id` and a `private_key`")
        repo = urlsplit(url).path.strip("/").removesuffix(".git")
        installation = self.config.get("installation_id")

        async def mint() -> str:
            import httpx

            from choregos_adapters.github.auth import GitHubAppAuth

            auth = GitHubAppAuth(
                app_id=app_id,
                private_key=key,
                base_url=str(self.config.get("api_url") or "https://api.github.com"),
            )
            async with httpx.AsyncClient(timeout=20.0) as http:
                issued = await auth.token_for(
                    http,
                    repo,
                    installation_id=int(installation) if installation else None,
                    permissions={"contents": "read"},
                )
            return issued.token

        return mint

    def source(self, config: dict[str, Any], *, key: str) -> GitSource:
        url = str(config["url"])
        return GitSource(
            url=url,
            ref=str(config.get("ref") or "main"),
            paths=config.get("paths"),
            credential=self._credential(url),
            host=self.host,
        )
