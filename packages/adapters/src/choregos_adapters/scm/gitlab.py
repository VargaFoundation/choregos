# SPDX-License-Identifier: Apache-2.0
"""The GitLab ScmAdapter: branches, commits, merge requests, reviewers, auto-merge, compare (ADR 0104).

A run never receives the configured token (Maintainer, `api`): `mint_token` creates a project access
token limited to `write_repository` that GitLab expires the next day. GitLab expires tokens on a
date, so a day is the finest lifetime there is; there is no revoke in the protocol, so expired run
tokens (and the bot users they created) are cleaned up at the next mint.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import quote

import structlog
from choregos_contracts import InboundEvent
from choregos_core.domain import CheckRun, DiffFile, DiffSummary, PrRef, PrState

from ..ci.gitlab import _JOB_RUN
from ..errors import ConfigurationError, UpstreamError
from ..http import RestClient
from ..tracker.gitlab_events import parse_gitlab_event

log = structlog.get_logger(__name__)

RUN_TOKEN_PREFIX = "choregos-run-"  # noqa: S105 - a name prefix, not a secret
#: The role of the run token: Developer. Its scope, not its role, keeps it to repository writes.
DEVELOPER_ACCESS_LEVEL = 30
#: Expired run tokens deleted per mint, so one call never turns into a long loop.
CLEANUP_LIMIT = 20
DRAFT_PREFIX = "Draft: "


class GitLabScm:
    """SCM on one GitLab project. The `repo` argument of the protocol is the configured project."""

    def __init__(self, client: RestClient, project: str, default_branch: str = "main") -> None:
        if not project:
            raise ConfigurationError("scm/gitlab: `project` is required")
        self.client = client
        self.project = project
        self.default_branch = default_branch

    @property
    def _base(self) -> str:
        return f"/api/v4/projects/{quote(self.project, safe='')}"

    def _mr(self, ref: PrRef) -> str:
        return f"{self._base}/merge_requests/{ref.number}"

    async def mint_token(self, repo: str, ttl_s: int, scopes: list[str]) -> str:
        """A project access token for one run: `write_repository` only, expiring tomorrow (UTC).

        The lifetime and the scopes asked by the caller are not honoured on purpose: GitLab counts
        in days, and the runner only ever needs to write the repository.
        """
        await self._delete_expired_run_tokens()
        tomorrow = datetime.now(UTC).date() + timedelta(days=1)
        created = await self.client.request(
            "POST",
            f"{self._base}/access_tokens",
            json={
                "name": f"{RUN_TOKEN_PREFIX}{uuid.uuid4().hex[:8]}",
                "scopes": ["write_repository"],
                "access_level": DEVELOPER_ACCESS_LEVEL,
                "expires_at": tomorrow.isoformat(),
            },
        )
        token = str((created or {}).get("token") or "")
        if not token:
            raise UpstreamError("gitlab", "the project access token answer carries no token")
        return token

    async def _delete_expired_run_tokens(self) -> None:
        """Best effort: a failure here must never keep a run from getting its token."""
        try:
            tokens = await self.client.request("GET", f"{self._base}/access_tokens", params={"per_page": 100})
            today = datetime.now(UTC).date()
            expired = [
                t
                for t in (tokens if isinstance(tokens, list) else [])
                if str(t.get("name", "")).startswith(RUN_TOKEN_PREFIX)
                and _expired(t.get("expires_at"), today)
            ]
            for old in expired[:CLEANUP_LIMIT]:
                await self.client.request("DELETE", f"{self._base}/access_tokens/{old['id']}")
        except Exception as exc:  # cleanup is best effort, minting goes on
            log.warning("gitlab_run_token_cleanup_failed", error=str(exc))

    async def ensure_branch(self, repo: str, name: str, base: str) -> None:
        try:
            await self.client.request(
                "POST", f"{self._base}/repository/branches", json={"branch": name, "ref": base}
            )
        except UpstreamError as exc:
            # GitLab answers 400 (not 409) when the branch exists.
            if exc.status_code == 400 and "already exists" in str(exc):
                return
            raise

    async def branch_exists(self, repo: str, name: str) -> bool:
        """Only a 404 means absent: any other error is raised, an outage is not an absence."""
        try:
            await self.client.request("GET", f"{self._base}/repository/branches/{quote(name, safe='')}")
        except UpstreamError as exc:
            if exc.status_code == 404:
                return False
            raise
        return True

    async def commit_files(self, repo: str, branch: str, files: dict[str, str], message: str) -> str:
        """One commit for all the files: `create` or `update` per file. Idempotent: files already
        identical on the branch are left out, and nothing is committed (empty sha) if none differs."""
        actions: list[dict[str, str]] = []
        for path, content in sorted(files.items()):
            url = f"{self._base}/repository/files/{quote(path, safe='')}"
            try:
                current = await self.client.request("GET", url, params={"ref": branch})
            except UpstreamError as exc:
                if exc.status_code != 404:
                    raise
                actions.append({"action": "create", "file_path": path, "content": content})
                continue
            if (
                current.get("encoding") == "base64"
                and base64.b64decode(current.get("content", "")) == content.encode()
            ):
                continue
            actions.append({"action": "update", "file_path": path, "content": content})
        if not actions:
            return ""
        commit = await self.client.request(
            "POST",
            f"{self._base}/repository/commits",
            json={"branch": branch, "commit_message": message, "actions": actions},
        )
        return str(commit.get("id", ""))

    async def open_pr(self, repo: str, head: str, base: str, title: str, body: str, draft: bool) -> PrRef:
        existing = await self.client.request(
            "GET",
            f"{self._base}/merge_requests",
            params={"source_branch": head, "state": "opened"},
        )
        if isinstance(existing, list) and existing:
            mr = existing[0]
        else:
            mr = await self.client.request(
                "POST",
                f"{self._base}/merge_requests",
                json={
                    "source_branch": head,
                    "target_branch": base,
                    "title": _titled(title, draft),
                    "description": body,
                },
            )
        return PrRef(repo=self.project, number=int(mr["iid"]), url=mr.get("web_url"), head=head, base=base)

    async def update_pr(self, ref: PrRef, body: str | None, draft: bool | None) -> None:
        payload: dict[str, Any] = {}
        if body is not None:
            payload["description"] = body
        if draft is not None:
            mr = await self.client.request("GET", self._mr(ref))
            payload["title"] = _titled(_untitled(str(mr.get("title", ""))), draft)
        if payload:
            await self.client.request("PUT", self._mr(ref), json=payload)

    async def comment_pr(self, ref: PrRef, body: str) -> str:
        note = await self.client.request("POST", f"{self._mr(ref)}/notes", json={"body": body})
        mr = await self.client.request("GET", self._mr(ref))
        # A note has no URL of its own: it is an anchor in the merge request's page.
        return f"{mr.get('web_url', '')}#note_{(note or {}).get('id', '')}"

    async def get_pr(self, ref: PrRef) -> PrState:
        mr = await self.client.request("GET", self._mr(ref))
        pipelines = await self.client.request("GET", f"{self._mr(ref)}/pipelines")
        diffs = await self.client.paginate(f"{self._mr(ref)}/diffs")
        checks: list[CheckRun] = []
        if isinstance(pipelines, list) and pipelines:
            latest = pipelines[0]  # newest first
            status, conclusion = _JOB_RUN.get(str(latest.get("status", "")), ("in_progress", None))
            checks.append(
                CheckRun(
                    name=f"pipeline #{latest.get('id', '')}",
                    status=status,
                    conclusion=conclusion,
                    url=latest.get("web_url"),
                )
            )
        detailed = mr.get("detailed_merge_status")
        mergeable: bool | None = None
        if detailed is not None:
            mergeable = detailed == "mergeable"
        elif mr.get("has_conflicts") is not None:
            mergeable = not mr["has_conflicts"]
        return PrState(
            ref=PrRef(
                repo=self.project,
                number=ref.number,
                url=mr.get("web_url"),
                head=mr.get("source_branch"),
                base=mr.get("target_branch"),
            ),
            title=str(mr.get("title", "")),
            body=mr.get("description") or "",
            draft=bool(mr.get("draft") or mr.get("work_in_progress")),
            merged=mr.get("state") == "merged",
            mergeable=mergeable,
            head_sha=mr.get("sha"),
            checks=checks,
            files=[str(d.get("new_path") or d.get("old_path")) for d in diffs],
            labels=[str(label) for label in mr.get("labels", [])],
        )

    async def request_review(self, ref: PrRef, reviewers: list[str]) -> None:
        ids: list[int] = []
        for username in reviewers:
            if "/" in username:  # a team: GitLab reviewers are users
                continue
            users = await self.client.request("GET", "/api/v4/users", params={"username": username})
            if isinstance(users, list) and users:
                ids.append(int(users[0]["id"]))
        if ids:
            await self.client.request("PUT", self._mr(ref), json={"reviewer_ids": ids})

    async def enqueue_merge(self, ref: PrRef) -> None:
        """Merge when the pipeline succeeds. Recent GitLab reads `auto_merge`, older reads
        `merge_when_pipeline_succeeds`: both are sent."""
        await self.client.request(
            "PUT",
            f"{self._mr(ref)}/merge",
            json={"auto_merge": True, "merge_when_pipeline_succeeds": True},
        )

    async def compare(self, repo: str, base: str, head: str) -> DiffSummary:
        payload = await self.client.request(
            "GET", f"{self._base}/repository/compare", params={"from": base, "to": head}
        )
        return DiffSummary(
            base=base,
            head=head,
            files=[
                DiffFile(
                    path=str(d.get("new_path") or d.get("old_path")),
                    status=_status(d),
                    additions=_count(d.get("diff", ""), "+"),
                    deletions=_count(d.get("diff", ""), "-"),
                    patch=d.get("diff"),
                )
                for d in (payload or {}).get("diffs", [])
            ],
        )

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> list[InboundEvent]:
        event = headers.get("X-Gitlab-Event", headers.get("x-gitlab-event", ""))
        delivery = headers.get("X-Gitlab-Event-UUID", headers.get("x-gitlab-event-uuid", ""))
        return parse_gitlab_event(event, delivery, json.loads(body or b"{}"))

    async def test(self) -> dict[str, Any]:
        project = await self.client.request("GET", self._base)
        return {"ok": bool(project), "project": self.project}


def _expired(expires_at: Any, today: date) -> bool:
    try:
        return date.fromisoformat(str(expires_at)) < today
    except ValueError:
        return False  # no date, or an unreadable one: keep the token


def _titled(title: str, draft: bool) -> str:
    return DRAFT_PREFIX + title if draft and not title.startswith(DRAFT_PREFIX) else title


def _untitled(title: str) -> str:
    return title.removeprefix(DRAFT_PREFIX)


def _status(diff: dict[str, Any]) -> Any:
    if diff.get("new_file"):
        return "added"
    if diff.get("deleted_file"):
        return "removed"
    if diff.get("renamed_file"):
        return "renamed"
    return "modified"


def _count(patch: str, marker: str) -> int:
    """GitLab diffs hold hunks only (no `---`/`+++` header), so each marked line is a change."""
    return sum(1 for line in str(patch).splitlines() if line.startswith(marker))
