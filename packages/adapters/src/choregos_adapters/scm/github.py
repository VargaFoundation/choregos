"""ScmAdapter GitHub : branches, PR, checks, reviews, merge queue, check-runs Choregos."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from choregos_contracts import InboundEvent
from choregos_core.domain import CheckRun, DiffFile, DiffSummary, PrRef, PrState, ReviewState

from ..errors import UpstreamError
from ..github.auth import RUNNER_PERMISSIONS
from ..github.client import GitHubClient
from ..tracker.github_events import parse_github_event

MARK_READY = """
mutation($id: ID!) { markPullRequestReadyForReview(input: {pullRequestId: $id}) { clientMutationId } }
"""

ENABLE_AUTO_MERGE = """
mutation($pullRequestId: ID!, $method: PullRequestMergeMethod!) {
  enablePullRequestAutoMerge(input: {pullRequestId: $pullRequestId, mergeMethod: $method}) {
    pullRequest { id number }
  }
}
"""


class GitHubScm:
    """SCM GitHub via l'App `choregos-bot`."""

    def __init__(
        self,
        client: GitHubClient,
        *,
        default_repo: str = "",
        merge_method: str = "SQUASH",
        use_merge_queue: bool = True,
    ) -> None:
        self.client = client
        self.default_repo = default_repo
        self.merge_method = merge_method
        self.use_merge_queue = use_merge_queue

    async def mint_token(self, repo: str, ttl_s: int, scopes: list[str]) -> str:
        """Jeton de dépôt, TTL court, permissions minimales : ce que le runner reçoit (§2.4)."""
        if self.client.auth is None:
            if self.client.static_token:
                return self.client.static_token
            raise UpstreamError("github", "aucune App configurée pour minter un jeton")
        permissions = {k: v for k, v in RUNNER_PERMISSIONS.items() if not scopes or k in scopes}
        token = await self.client.auth.token_for(
            self.client._client, repo, permissions=permissions or RUNNER_PERMISSIONS, ttl_s=ttl_s
        )
        return token.token

    async def ensure_branch(self, repo: str, name: str, base: str) -> None:
        try:
            await self.client.request("GET", f"/repos/{repo}/git/ref/heads/{name}", repo=repo)
            return
        except UpstreamError as exc:
            if exc.status_code != 404:
                raise
        base_ref = await self.client.request("GET", f"/repos/{repo}/git/ref/heads/{base}", repo=repo)
        await self.client.request(
            "POST",
            f"/repos/{repo}/git/refs",
            repo=repo,
            json={"ref": f"refs/heads/{name}", "sha": base_ref["object"]["sha"]},
        )

    async def open_pr(self, repo: str, head: str, base: str, title: str, body: str, draft: bool) -> PrRef:
        owner = repo.split("/", 1)[0]
        existing = await self.client.request(
            "GET", f"/repos/{repo}/pulls", repo=repo, params={"head": f"{owner}:{head}", "state": "open"}
        )
        if existing:
            pr = existing[0]
        else:
            pr = await self.client.request(
                "POST",
                f"/repos/{repo}/pulls",
                repo=repo,
                json={"title": title, "head": head, "base": base, "body": body, "draft": draft},
            )
        return PrRef(repo=repo, number=int(pr["number"]), url=pr.get("html_url"), head=head, base=base)

    async def update_pr(self, ref: PrRef, body: str | None, draft: bool | None) -> None:
        payload: dict[str, Any] = {}
        if body is not None:
            payload["body"] = body
        if payload:
            await self.client.request(
                "PATCH", f"/repos/{ref.repo}/pulls/{ref.number}", repo=ref.repo, json=payload
            )
        if draft is False:
            pr = await self.client.request("GET", f"/repos/{ref.repo}/pulls/{ref.number}", repo=ref.repo)
            await self.client.graphql(
                MARK_READY,
                {"id": pr["node_id"]},
                repo=ref.repo,
            )

    async def comment_pr(self, ref: PrRef, body: str) -> str:
        """Commente une PR. C'est par là que passent les ordres Atlantis (`atlantis apply`)."""
        comment = await self.client.request(
            "POST",
            f"/repos/{ref.repo}/issues/{ref.number}/comments",
            repo=ref.repo,
            json={"body": body},
        )
        return str(comment.get("html_url", ""))

    async def get_pr(self, ref: PrRef) -> PrState:
        pr = await self.client.request("GET", f"/repos/{ref.repo}/pulls/{ref.number}", repo=ref.repo)
        sha = pr["head"]["sha"]
        checks_payload = await self.client.request(
            "GET", f"/repos/{ref.repo}/commits/{sha}/check-runs", repo=ref.repo
        )
        reviews = await self.client.paginate(f"/repos/{ref.repo}/pulls/{ref.number}/reviews", repo=ref.repo)
        files = await self.client.paginate(f"/repos/{ref.repo}/pulls/{ref.number}/files", repo=ref.repo)
        return PrState(
            ref=PrRef(
                repo=ref.repo,
                number=ref.number,
                url=pr.get("html_url"),
                head=pr["head"]["ref"],
                base=pr["base"]["ref"],
            ),
            title=pr.get("title", ""),
            body=pr.get("body") or "",
            draft=bool(pr.get("draft")),
            merged=bool(pr.get("merged")),
            mergeable=pr.get("mergeable"),
            head_sha=sha,
            checks=[
                CheckRun(
                    name=check["name"],
                    status=check.get("status", "completed"),
                    conclusion=check.get("conclusion"),
                    url=check.get("html_url"),
                )
                for check in (checks_payload or {}).get("check_runs", [])
            ],
            reviews=[
                ReviewState(
                    reviewer=(review.get("user") or {}).get("login", ""),
                    state=(review.get("state") or "commented").lower(),
                    body=review.get("body") or "",
                )
                for review in reviews
                if (review.get("state") or "").lower()
                in {"approved", "changes_requested", "commented", "dismissed"}
            ],
            files=[f["filename"] for f in files],
            labels=[label["name"] for label in pr.get("labels", [])],
        )

    async def request_review(self, ref: PrRef, reviewers: list[str]) -> None:
        if not reviewers:
            return
        await self.client.request(
            "POST",
            f"/repos/{ref.repo}/pulls/{ref.number}/requested_reviewers",
            repo=ref.repo,
            json={
                "reviewers": [r for r in reviewers if "/" not in r],
                "team_reviewers": [r.split("/")[-1] for r in reviewers if "/" in r],
            },
        )

    async def enqueue_merge(self, ref: PrRef) -> None:
        """Merge queue : `enablePullRequestAutoMerge`, sinon merge direct si la politique l'autorise."""
        pr = await self.client.request("GET", f"/repos/{ref.repo}/pulls/{ref.number}", repo=ref.repo)
        if self.use_merge_queue:
            await self.client.graphql(
                ENABLE_AUTO_MERGE,
                {"pullRequestId": pr["node_id"], "method": self.merge_method},
                repo=ref.repo,
            )
            return
        await self.client.request(
            "PUT",
            f"/repos/{ref.repo}/pulls/{ref.number}/merge",
            repo=ref.repo,
            json={"merge_method": self.merge_method.lower()},
        )

    async def compare(self, repo: str, base: str, head: str) -> DiffSummary:
        payload = await self.client.request("GET", f"/repos/{repo}/compare/{base}...{head}", repo=repo)
        return DiffSummary(
            base=base,
            head=head,
            files=[
                DiffFile(
                    path=f["filename"],
                    status=_map_status(f.get("status", "modified")),
                    additions=int(f.get("additions", 0)),
                    deletions=int(f.get("deletions", 0)),
                    patch=f.get("patch"),
                )
                for f in payload.get("files", [])
            ],
        )

    async def create_check_run(
        self,
        repo: str,
        sha: str,
        name: str,
        conclusion: str,
        summary: str,
        annotations: list[str] | None = None,
    ) -> None:
        """Check-runs `choregos/scope` et `choregos/evidence` (S3-05)."""
        await self.client.request(
            "POST",
            f"/repos/{repo}/check-runs",
            repo=repo,
            json={
                "name": name,
                "head_sha": sha,
                "status": "completed",
                "conclusion": conclusion,
                "output": {
                    "title": name,
                    "summary": summary,
                    "annotations": [
                        {
                            "path": path,
                            "start_line": 1,
                            "end_line": 1,
                            "annotation_level": "failure",
                            "message": "fichier hors du périmètre autorisé",
                        }
                        for path in (annotations or [])[:50]
                    ],
                },
            },
        )

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> list[InboundEvent]:
        import json

        event = headers.get("X-GitHub-Event", headers.get("x-github-event", ""))
        delivery = headers.get("X-GitHub-Delivery", headers.get("x-github-delivery", ""))
        return parse_github_event(event, delivery, json.loads(body or b"{}"))

    async def test(self) -> dict[str, Any]:
        repo = await self.client.request("GET", f"/repos/{self.default_repo}", repo=self.default_repo)
        return {"ok": True, "repo": repo.get("full_name"), "default_branch": repo.get("default_branch")}


def _map_status(status: str) -> str:
    return {"added": "added", "removed": "removed", "renamed": "renamed"}.get(status, "modified")
