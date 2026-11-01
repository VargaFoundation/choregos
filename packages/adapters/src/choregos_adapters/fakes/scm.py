# SPDX-License-Identifier: Apache-2.0
"""In-memory SCM: branches, PRs, checks, reviews, merge queue, diffs."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from choregos_contracts import InboundEvent, InboundEventType
from choregos_core.domain import CheckRun, DiffFile, DiffSummary, PrRef, PrState, ReviewState


class FakeScm:
    """Test SCM. Diffs are scripted by `set_diff()`."""

    def __init__(self) -> None:
        self.branches: dict[str, dict[str, str]] = {}
        self.prs: dict[tuple[str, int], PrState] = {}
        self.merge_queue: list[PrRef] = []
        self.tokens_minted: list[tuple[str, int]] = []
        self._next_number = 1
        self._diffs: dict[tuple[str, str, str], DiffSummary] = {}
        self.check_runs: list[dict[str, Any]] = []
        self.comments: list[tuple[str, int, str]] = []
        #: (repository, branch) → {path: content} written by `commit_files`, and the commits in order.
        self.files: dict[tuple[str, str], dict[str, str]] = {}
        self.commits: list[tuple[str, str, str, list[str]]] = []
        # Scriptable hooks: a simulated Atlantis answers the `atlantis apply` comment.
        self.on_comment: list[Any] = []

    # ───────────────────────── scripting ─────────────────────────

    def set_diff(self, repo: str, base: str, head: str, files: list[tuple[Any, ...]]) -> None:
        """`(path, additions, deletions)`, or `(path, additions, deletions, patch)` when a guarantee
        reads what the branch writes (`markdown_sections`)."""
        self._diffs[(repo, base, head)] = DiffSummary(
            base=base,
            head=head,
            files=[
                DiffFile(path=f[0], additions=f[1], deletions=f[2], patch=f[3] if len(f) > 3 else None)
                for f in files
            ],
        )

    def delete_branch(self, repo: str, name: str) -> None:
        """The work branch was never pushed: the implementation changed nothing (S22-46)."""
        self.branches.get(repo, {}).pop(name, None)
        self.files.pop((repo, name), None)
        for key in [k for k in self._diffs if k[0] == repo and k[2] == name]:
            del self._diffs[key]

    def atlantis(self, conclusion: str = "success") -> None:
        """Simulates Atlantis: an `atlantis apply` sets the `atlantis/apply` check."""

        def hook(ref: PrRef, body: str) -> None:
            if "atlantis apply" not in body:
                return
            pr = self.prs[(ref.repo, ref.number)]
            pr.checks = [c for c in pr.checks if c.name != "atlantis/apply"]
            pr.checks.append(CheckRun(name="atlantis/apply", status="completed", conclusion=conclusion))

        self.on_comment.append(hook)

    def set_checks(self, ref: PrRef, conclusion: str, *, scanners: bool = True) -> None:
        """Sets the PR's check runs. By default, the project has its scanners too."""
        names = ["ci", *(["semgrep", "trivy", "gitleaks"] if scanners else [])]
        pr = self.prs[(ref.repo, ref.number)]
        pr.checks = [CheckRun(name=name, status="completed", conclusion=conclusion) for name in names]

    def submit_review(self, ref: PrRef, reviewer: str, state: str) -> None:
        pr = self.prs[(ref.repo, ref.number)]
        pr.reviews.append(ReviewState(reviewer=reviewer, state=state))

    def merge(self, ref: PrRef) -> None:
        self.prs[(ref.repo, ref.number)].merged = True

    # ───────────────────────── ScmAdapter ─────────────────────────

    async def mint_token(self, repo: str, ttl_s: int, scopes: list[str]) -> str:
        self.tokens_minted.append((repo, ttl_s))
        return f"fake-token-{repo.replace('/', '-')}-{ttl_s}"

    async def ensure_branch(self, repo: str, name: str, base: str) -> None:
        self.branches.setdefault(repo, {})[name] = base

    async def branch_exists(self, repo: str, name: str) -> bool:
        """A branch exists once created (`ensure_branch`), written to (`commit_files`) or given a
        scripted diff (`set_diff`): what a pushed work branch looks like here."""
        return (
            name in self.branches.get(repo, {})
            or (repo, name) in self.files
            or any(r == repo and head == name for (r, _base, head) in self._diffs)
        )

    async def commit_files(self, repo: str, branch: str, files: dict[str, str], message: str) -> str:
        """Like GitHub: an identical file is not rewritten, a replay creates no commit."""
        tree = self.files.setdefault((repo, branch), {})
        changes = {path: content for path, content in files.items() if tree.get(path) != content}
        if changes:
            tree.update(changes)
            self.commits.append((repo, branch, message, sorted(changes)))
        return f"commit-{len(self.commits)}"

    async def open_pr(self, repo: str, head: str, base: str, title: str, body: str, draft: bool) -> PrRef:
        # Like GitHub (`GitHubScm.open_pr`): a PR already open on the same branch is reused.
        for pr in self.prs.values():
            if pr.ref.repo == repo and pr.ref.head == head and not pr.merged:
                return pr.ref
        number = self._next_number
        self._next_number += 1
        ref = PrRef(
            repo=repo, number=number, url=f"https://fake.scm/{repo}/pull/{number}", head=head, base=base
        )
        self.prs[(repo, number)] = PrState(
            ref=ref, title=title, body=body, draft=draft, head_sha=f"sha-{number}"
        )
        return ref

    async def update_pr(self, ref: PrRef, body: str | None, draft: bool | None) -> None:
        pr = self.prs[(ref.repo, ref.number)]
        if body is not None:
            pr.body = body
        if draft is not None:
            pr.draft = draft

    async def comment_pr(self, ref: PrRef, body: str) -> str:
        self.comments.append((ref.repo, ref.number, body))
        for hook in self.on_comment:
            hook(ref, body)
        return f"https://fake.scm/{ref.repo}/pull/{ref.number}#comment-{len(self.comments)}"

    async def get_pr(self, ref: PrRef) -> PrState:
        return self.prs[(ref.repo, ref.number)].model_copy(deep=True)

    async def request_review(self, ref: PrRef, reviewers: list[str]) -> None:
        self.prs[(ref.repo, ref.number)].labels.append("review-requested")

    async def enqueue_merge(self, ref: PrRef) -> None:
        self.merge_queue.append(ref)

    async def compare(self, repo: str, base: str, head: str) -> DiffSummary:
        return self._diffs.get((repo, base, head), DiffSummary(base=base, head=head))

    async def list_files(self, repo: str, ref: str, directory: str) -> list[str]:
        """The paths under `directory` on `ref`: the ones `commit_files` wrote there."""
        prefix = directory.rstrip("/") + "/"
        return sorted(p for p in self.files.get((repo, ref), {}) if p.startswith(prefix))

    async def create_check_run(
        self,
        repo: str,
        sha: str,
        name: str,
        conclusion: str,
        summary: str,
        annotations: list[str] | None = None,
    ) -> None:
        self.check_runs.append(
            {
                "repo": repo,
                "sha": sha,
                "name": name,
                "conclusion": conclusion,
                "summary": summary,
                "annotations": list(annotations or []),
            }
        )

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> list[InboundEvent]:
        payload = json.loads(body.decode("utf-8"))
        if "type" in payload:
            return [InboundEvent.model_validate(payload)]
        return [
            InboundEvent(
                type=InboundEventType.PR_OPENED,
                source="fake",
                delivery_id=headers.get("X-Delivery", "d1"),
                payload=payload,
            )
        ]
