# SPDX-License-Identifier: Apache-2.0
"""nginx: its configuration as the GitOps repository holds it — never the live server (ADR 0052).

The repository is the source of truth (ADR 0009): what runs is what was merged. So the connector
reads files under one root of one repository, at one ref, through the forge's API with the
platform's credentials — an agent never receives them. Validation (`nginx -t`) is not here: it
needs the nginx binary and the whole configuration tree, which the runner has in its workspace;
it is the runner-side tool `validate_nginx_config` (`choregos_tools_mcp`).

A change is `propose_nginx_config` (S33-09): a pull request to the same repository — a governed
action a person approves —, carried to the servers by the release train once merged.
"""

from __future__ import annotations

import base64
import posixpath
from typing import Any, Protocol

from ..errors import AdapterError, UpstreamError
from ..registry import OperationSpec
from .gitops import GitOpsTarget, propose_change

_TEXT: dict[str, Any] = {"type": "string"}
#: A file larger than this is not configuration an agent should read whole.
MAX_FILE_BYTES = 256 * 1024

NGINX_READ_OPERATIONS: tuple[OperationSpec, ...] = (
    OperationSpec(
        "list_nginx_files",
        "read",
        "lists the nginx configuration files under the connector's root in the GitOps repository",
        {"type": "object", "properties": {"path": _TEXT}, "required": [], "additionalProperties": False},
    ),
    OperationSpec(
        "read_nginx_file",
        "read",
        "reads one nginx configuration file from the GitOps repository, as merged",
        {
            "type": "object",
            "properties": {"path": _TEXT},
            "required": ["path"],
            "additionalProperties": False,
        },
    ),
)


NGINX_WRITE_OPERATIONS: tuple[OperationSpec, ...] = (
    OperationSpec(
        "propose_nginx_config",
        "write",
        "proposes an nginx configuration file as a pull request to the GitOps repository (validate it with "
        "`validate_nginx_config` first); nothing is applied to a server",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "the file, under the connector's root"},
                "content": {"type": "string", "description": "the file's new content, whole"},
                "title": {"type": "string", "maxLength": 200},
                "rationale": {"type": "string", "description": "why: the findings and the risk"},
            },
            "required": ["path", "content", "title", "rationale"],
            "additionalProperties": False,
        },
        human_approval=True,
        platform_arguments=("action_key", "work_item_key"),
    ),
)


class RepoReader(Protocol):
    async def list_files(self, prefix: str) -> list[str]: ...
    async def read_file(self, path: str) -> str: ...


class GitHubRepoReader:
    """Files of one repository at one ref, through the GitHub REST API (trees and contents)."""

    def __init__(self, client: Any, repo: str, ref: str = "main") -> None:
        self.client, self.repo, self.ref = client, repo, ref

    async def list_files(self, prefix: str) -> list[str]:
        tree = await self.client.request(
            "GET", f"/repos/{self.repo}/git/trees/{self.ref}", repo=self.repo, params={"recursive": "1"}
        )
        return sorted(
            str(entry["path"])
            for entry in tree.get("tree") or []
            if entry.get("type") == "blob" and str(entry.get("path", "")).startswith(prefix)
        )

    async def read_file(self, path: str) -> str:
        try:
            content = await self.client.request(
                "GET", f"/repos/{self.repo}/contents/{path}", repo=self.repo, params={"ref": self.ref}
            )
        except UpstreamError as error:
            if error.status_code == 404:
                raise AdapterError(f"nginx: no file {path} in {self.repo}@{self.ref}") from error
            raise
        if content.get("encoding") != "base64":
            raise AdapterError(f"nginx: {path} is not a file")
        return base64.b64decode(content.get("content", "")).decode("utf-8", errors="replace")


def confined(root: str, path: str, service: str = "nginx") -> str:
    """`path`, normalised, inside `root` — or a refusal: no `..`, no absolute path out of it."""
    root = root.strip("/")
    relative = path.strip().lstrip("/")

    def inside(candidate: str) -> bool:
        if candidate.startswith(".."):
            return False
        return not root or candidate == root or candidate.startswith(f"{root}/")

    # As given (`nginx/conf.d/api.conf`), then relative to the root (`conf.d/api.conf`).
    for candidate in (posixpath.normpath(relative), posixpath.normpath(posixpath.join(root, relative))):
        if inside(candidate):
            return candidate
    raise AdapterError(f"{service}: {path} is outside the configuration root `{root}/`")


class NginxConfig:
    service = "nginx"
    operations: tuple[OperationSpec, ...] = NGINX_READ_OPERATIONS + NGINX_WRITE_OPERATIONS

    def __init__(
        self, reader: RepoReader, *, root: str = "nginx", gitops: GitOpsTarget | None = None
    ) -> None:
        self.reader = reader
        self.root = root.strip("/")
        self.gitops = gitops

    async def list_nginx_files(self, path: str | None = None) -> dict[str, Any]:
        prefix = confined(self.root, path) if path else self.root
        files = await self.reader.list_files(f"{prefix}/" if prefix else "")
        return {"root": self.root, "files": files, "count": len(files)}

    async def read_nginx_file(self, path: str) -> dict[str, Any]:
        target = confined(self.root, path)
        content = await self.reader.read_file(target)
        if len(content.encode()) > MAX_FILE_BYTES:
            raise AdapterError(f"nginx: {target} is larger than {MAX_FILE_BYTES // 1024} KiB")
        return {"path": target, "content": content}

    async def propose_nginx_config(
        self,
        path: str,
        content: str,
        title: str,
        rationale: str,
        action_key: str,
        work_item_key: str | None = None,
    ) -> dict[str, Any]:
        if self.gitops is None:
            raise AdapterError("nginx: no repository to propose to")
        if not content.strip():
            raise AdapterError("nginx: an empty configuration is not proposed")
        target = confined(self.root, path)
        return await propose_change(
            self.gitops,
            files={target: content if content.endswith("\n") else content + "\n"},
            title=title,
            rationale=rationale,
            action_key=action_key,
            work_item_key=work_item_key,
        )

    async def executer(self, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        if operation not in {o.name for o in self.operations}:
            raise AdapterError(f"nginx has no operation {operation}")
        try:
            result: dict[str, Any] = await getattr(self, operation)(**params)
        except TypeError as error:
            raise AdapterError(f"nginx {operation}: {error}") from error
        return result

    async def test(self) -> dict[str, Any]:
        files = await self.reader.list_files(f"{self.root}/" if self.root else "")
        return {"ok": True, "root": self.root, "files": len(files)}
