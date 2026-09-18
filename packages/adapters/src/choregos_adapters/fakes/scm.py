"""SCM en mémoire : branches, PR, checks, reviews, merge queue, diffs."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from choregos_contracts import InboundEvent, InboundEventType
from choregos_core.domain import CheckRun, DiffFile, DiffSummary, PrRef, PrState, ReviewState


class FakeScm:
    """SCM de test. Les diffs sont scriptés par `set_diff()`."""

    def __init__(self) -> None:
        self.branches: dict[str, dict[str, str]] = {}
        self.prs: dict[tuple[str, int], PrState] = {}
        self.merge_queue: list[PrRef] = []
        self.tokens_minted: list[tuple[str, int]] = []
        self._next_number = 1
        self._diffs: dict[tuple[str, str, str], DiffSummary] = {}
        self.check_runs: list[dict[str, Any]] = []

    # ───────────────────────── scripting ─────────────────────────

    def set_diff(self, repo: str, base: str, head: str, files: list[tuple[str, int, int]]) -> None:
        self._diffs[(repo, base, head)] = DiffSummary(
            base=base,
            head=head,
            files=[DiffFile(path=p, additions=a, deletions=d) for p, a, d in files],
        )

    def set_checks(self, ref: PrRef, conclusion: str, *, scanners: bool = True) -> None:
        """Pose les check-runs de la PR. Par défaut, le projet a aussi ses scanners."""
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

    async def open_pr(self, repo: str, head: str, base: str, title: str, body: str, draft: bool) -> PrRef:
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

    async def get_pr(self, ref: PrRef) -> PrState:
        return self.prs[(ref.repo, ref.number)].model_copy(deep=True)

    async def request_review(self, ref: PrRef, reviewers: list[str]) -> None:
        self.prs[(ref.repo, ref.number)].labels.append("review-requested")

    async def enqueue_merge(self, ref: PrRef) -> None:
        self.merge_queue.append(ref)

    async def compare(self, repo: str, base: str, head: str) -> DiffSummary:
        return self._diffs.get((repo, base, head), DiffSummary(base=base, head=head))

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
