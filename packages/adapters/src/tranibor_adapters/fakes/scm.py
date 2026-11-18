# SPDX-License-Identifier: Apache-2.0
"""In-memory SCM: branches, PRs, checks, reviews, merge queue, diffs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from tranibor_contracts import InboundEvent, InboundEventType
from tranibor_core.domain import CheckRun, DiffFile, DiffSummary, PrRef, PrState, ReviewState

from ..errors import HeadMovedError


class FakeScm:
    """Test SCM. Diffs are scripted by `set_diff()`.

    A branch has a head commit, as on a forge: every write to it (`set_diff`, `commit_files`,
    `push`) gives it a new one, a commit keeps the diff it was pushed with, and a merge names the
    commit it merges (ADR 0171)."""

    capabilities: frozenset[str] = frozenset({"merge_at_commit"})

    def __init__(self) -> None:
        self.branches: dict[str, dict[str, str]] = {}
        self.prs: dict[tuple[str, int], PrState] = {}
        self.merge_queue: list[PrRef] = []
        #: (repository, branch) → the commit its head is at.
        self.heads: dict[tuple[str, str], str] = {}
        #: commit → its repository and the diffs it holds, by base: what `compare` answers for it.
        self._commits: dict[str, tuple[str, dict[str, DiffSummary]]] = {}
        #: (repository, number) → the commit the pull request was merged at.
        self.merged_at: dict[tuple[str, int], str] = {}
        # Scriptable hooks, called when a merge arrives, before the forge looks at it: a push that races it.
        self.before_merge: list[Any] = []
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
        self._move_head(repo, head)

    def push(self, repo: str, branch: str, files: list[tuple[Any, ...]] | None = None) -> str:
        """A commit lands on the branch and its head moves; returns the new head. With `files`, the
        commit also changes what the branch holds against `main`, as `set_diff` scripts it."""
        if files is not None:
            self.set_diff(repo, "main", branch, files)
            return self.heads[(repo, branch)]
        return self._move_head(repo, branch)

    def _move_head(self, repo: str, branch: str) -> str:
        """A new commit at the head of the branch, holding the diffs scripted for it so far."""
        sha = hashlib.sha1(  # a commit id, not a secret
            f"{repo}:{branch}:{len(self._commits)}".encode(), usedforsecurity=False
        ).hexdigest()
        self.heads[(repo, branch)] = sha
        self._commits[sha] = (
            repo,
            {base: diff for (r, base, head), diff in self._diffs.items() if r == repo and head == branch},
        )
        return sha

    def delete_branch(self, repo: str, name: str) -> None:
        """The work branch was never pushed: the implementation changed nothing (S22-46)."""
        self.branches.get(repo, {}).pop(name, None)
        self.files.pop((repo, name), None)
        self.heads.pop((repo, name), None)
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

    async def head_of(self, repo: str, branch: str) -> str | None:
        """The commit the branch's head is at; `None` for a branch that neither exists nor carries
        a pull request. A branch nothing wrote to yet gets its first commit here."""
        if (repo, branch) in self.heads:
            return self.heads[(repo, branch)]
        if not await self.branch_exists(repo, branch):
            return None
        return self._move_head(repo, branch)

    async def commit_files(self, repo: str, branch: str, files: dict[str, str], message: str) -> str:
        """Like GitHub: an identical file is not rewritten, a replay creates no commit."""
        tree = self.files.setdefault((repo, branch), {})
        changes = {path: content for path, content in files.items() if tree.get(path) != content}
        if changes:
            tree.update(changes)
            self.commits.append((repo, branch, message, sorted(changes)))
            self._move_head(repo, branch)
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
        # A pull request has a head commit even when the test pushed nothing to its branch.
        head_sha = self.heads.get((repo, head)) or self._move_head(repo, head)
        self.prs[(repo, number)] = PrState(ref=ref, title=title, body=body, draft=draft, head_sha=head_sha)
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
        """The pull request as it stands: its head follows its branch until it is merged."""
        key = (ref.repo, ref.number)
        pr = self.prs[key].model_copy(deep=True)
        pr.head_sha = (
            self.merged_at.get(key) or self.heads.get((pr.ref.repo, pr.ref.head or "")) or pr.head_sha
        )
        return pr

    async def request_review(self, ref: PrRef, reviewers: list[str]) -> None:
        self.prs[(ref.repo, ref.number)].labels.append("review-requested")

    async def enqueue_merge(self, ref: PrRef, sha: str) -> None:
        """Like the forges: the merge names a commit and is refused when the pull request's head is
        elsewhere. Merged at that commit already, the pull request is left as it is (a replay)."""
        for hook in self.before_merge:
            hook(ref)
        key = (ref.repo, ref.number)
        head = (await self.get_pr(ref)).head_sha
        if head != sha:
            raise HeadMovedError("fake", expected=sha, actual=head)
        if key in self.merged_at:
            return
        self.merged_at[key] = sha
        self.merge_queue.append(ref)

    async def compare(self, repo: str, base: str, head: str) -> DiffSummary:
        """`head` is a commit (the diff that commit was pushed with) or a branch (what it holds now)."""
        commit = self._commits.get(head)
        if commit is not None and commit[0] == repo:
            found = commit[1].get(base)
            return found.model_copy(update={"head": head}) if found else DiffSummary(base=base, head=head)
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
