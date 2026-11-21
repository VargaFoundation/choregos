# SPDX-License-Identifier: Apache-2.0
"""The Azure Repos ScmAdapter: branches, pushes, pull requests, votes, completion, statuses, compare (S13-08).

One repository of one Azure DevOps team project. What differs from the other forges, and what this
adapter decides about it:

* **A pull request completes, it is not "merged".** `enqueue_merge` completes it at the commit the gates
  judged (`lastMergeSourceCommit`, ADR 0171), with the connector's merge strategy, **auto-complete off**
  and `bypassPolicy` never set: the branch policies of the repository still bind. A head that moved is a
  `HeadMovedError`, and a pull request already completed at that commit is left as it is.
* **A reviewer's vote is a number.** 10 and 5 approve, -5 (waiting for the author) and -10 (rejected) ask for
  changes, 0 is no review; a group of reviewers (a team) votes for no one and is left out.
* **The checks of a pull request** are its statuses of the current iteration (a status of an older iteration
  is stale), its Build and Status branch policies, and the builds of its source branch at its head commit.
  A build's result is read as `ci/azure_pipelines.py` documents.
* **No scoped token is minted.** Azure DevOps has no call that makes a short-lived, repository-scoped
  credential, and the connector's own credential reaches the whole project. A run is given the connector's
  `run_token` (a personal access token of an identity limited to *Code: Read & write*, held sealed) or
  nothing: without one `mint_token` refuses, a run never receives the connector's own credential.
"""

from __future__ import annotations

import asyncio
import difflib
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

import structlog
from tranibor_contracts import InboundEvent
from tranibor_core.domain import CheckRun, DiffFile, DiffSummary, PrRef, PrState, ReviewState

from ..azure_devops import SHA, ZERO_SHA, segment
from ..azure_devops_events import parse_azure_devops_event
from ..ci.azure_pipelines import (
    Conclusion,
    build_name_of,
    check_of,
    latest_per_pipeline,
    source_commit_of,
    web_url_of,
)
from ..errors import ConfigurationError, HeadMovedError, UpstreamError
from ..http import RestClient

log = structlog.get_logger(__name__)

#: A pull request's vote, as the reviewer review states.
APPROVING_VOTES = frozenset({10, 5})
REFUSING_VOTES = frozenset({-10, -5})
#: The policy types whose evaluation is a check: Build and Status (Azure DevOps' own ids).
BUILD_POLICY = "0609b952-1397-4640-95ec-e00a01b2c241"
STATUS_POLICY = "cbdc66da-9728-4af8-aada-9a5a32e4a226"
_STATUS_CHECK = {
    "succeeded": ("completed", "success"),
    "failed": ("completed", "failure"),
    "error": ("completed", "failure"),
    "pending": ("in_progress", None),
    "notApplicable": ("completed", "skipped"),
    "notSet": ("queued", None),
}
_EVALUATION_CHECK = {
    "approved": ("completed", "success"),
    "rejected": ("completed", "failure"),
    "broken": ("completed", "failure"),
    "running": ("in_progress", None),
    "queued": ("queued", None),
    "notApplicable": ("completed", "skipped"),
}
_COMMIT_STATE = {"success": "succeeded", "failure": "failed", "pending": "pending", "error": "error"}
_CHANGE_STATUS = {"add": "added", "delete": "removed", "rename": "renamed"}
_PAGE = 1000
_MAX_CHANGE_PAGES = 20
_BUILDS_READ = 100
Sleep = Callable[[float], Awaitable[None]]


class AzureReposScm:
    """SCM on one Azure Repos repository. The `repo` argument of the protocol is the configured one."""

    #: A completion names its commit: `lastMergeSourceCommit`.
    capabilities: frozenset[str] = frozenset({"merge_at_commit"})

    def __init__(  # noqa: PLR0913 - one connector's configuration
        self,
        client: RestClient,
        project: str,
        repository: str,
        *,
        default_branch: str = "main",
        merge_strategy: str = "squash",
        delete_source_branch: bool = False,
        run_token: str = "",
        site_url: str | None = None,
        partially_succeeded: Conclusion = "failure",
        completion_polls: int = 5,
        sleep: Sleep = asyncio.sleep,
        diff_files_max: int = 200,
        diff_file_bytes_max: int = 1_000_000,
    ) -> None:
        if not project or not repository:
            raise ConfigurationError("scm/azure-repos: `project` and `repository` are required")
        self.client = client
        self.project = project
        self.repository = repository
        self.default_branch = default_branch
        self.merge_strategy = merge_strategy
        self.delete_source_branch = delete_source_branch
        self.run_token = run_token
        self.site_url = (site_url or client.base_url).rstrip("/")
        self.partial: Conclusion = "neutral" if partially_succeeded == "neutral" else "failure"
        self.completion_polls = completion_polls
        self._sleep = sleep
        self.diff_files_max = diff_files_max
        self.diff_file_bytes_max = diff_file_bytes_max
        self._project_id: str | None = None

    # ───────────────────────── addresses ─────────────────────────

    @property
    def _scope(self) -> str:
        return f"/{segment(self.project)}/_apis"

    @property
    def _git(self) -> str:
        return f"{self._scope}/git/repositories/{segment(self.repository)}"

    def _pr(self, ref: PrRef) -> str:
        return f"{self._git}/pullrequests/{ref.number}"

    def clone_url(self) -> str:
        """The https clone URL, without a credential: the runner adds the run's token itself."""
        return f"{self.site_url}/{segment(self.project)}/_git/{segment(self.repository)}"

    def web_url(self, number: int) -> str:
        return f"{self.clone_url()}/pullrequest/{number}"

    # ───────────────────────── token ─────────────────────────

    async def mint_token(self, repo: str, ttl_s: int, scopes: list[str]) -> str:
        """The connector's `run_token`, or a refusal. See the module's page for why nothing is minted."""
        if not self.run_token:
            raise ConfigurationError(
                "scm/azure-repos mints no token: Azure DevOps cannot make a repository-scoped, short-lived "
                "credential, and the connector's own reaches the whole project. Give the connector a "
                "`run_token` (a personal access token limited to Code: Read & write) so runs can "
                "clone and push"
            )
        return self.run_token

    # ───────────────────────── branches and files ─────────────────────────

    async def head_of(self, repo: str, branch: str) -> str | None:
        """The commit `branch` points at now, `None` when there is no such branch (ADR 0171). Any other
        error is raised: an outage is not an absence."""
        found = await self.client.request("GET", f"{self._git}/refs", params={"filter": f"heads/{branch}"})
        wanted = f"refs/heads/{branch}"
        for ref in (found or {}).get("value", []):
            if ref.get("name") == wanted:
                sha = str(ref.get("objectId") or "")
                if not sha:
                    raise UpstreamError("azure-repos", f"the branch `{branch}` names no commit")
                return sha
        return None

    async def branch_exists(self, repo: str, name: str) -> bool:
        return await self.head_of(repo, name) is not None

    async def ensure_branch(self, repo: str, name: str, base: str) -> None:
        if await self.head_of(repo, name) is not None:
            return
        start = await self.head_of(repo, base)
        if start is None:
            raise UpstreamError("azure-repos", f"the base branch `{base}` does not exist")
        updated = await self.client.request(
            "POST",
            f"{self._git}/refs",
            json=[{"name": f"refs/heads/{name}", "oldObjectId": ZERO_SHA, "newObjectId": start}],
        )
        for result in (updated or {}).get("value", []):
            if not result.get("success") and await self.head_of(repo, name) is None:
                raise UpstreamError(
                    "azure-repos", f"the branch `{name}` was not created: {result.get('updateStatus')}"
                )

    async def commit_files(self, repo: str, branch: str, files: dict[str, str], message: str) -> str:
        """One push, one commit, for all the files: `add` or `edit` per file. Idempotent: files already
        identical on the branch are left out, and nothing is pushed (empty sha) if none differs."""
        head = await self.head_of(repo, branch)
        if head is None:
            raise UpstreamError("azure-repos", f"the branch `{branch}` does not exist")
        changes: list[dict[str, Any]] = []
        for path, content in sorted(files.items()):
            current = await self._content(path, branch, "branch")
            if current == content:
                continue
            changes.append(
                {
                    "changeType": "add" if current is None else "edit",
                    "item": {"path": f"/{path.lstrip('/')}"},
                    "newContent": {"content": content, "contentType": "rawtext"},
                }
            )
        if not changes:
            return ""
        pushed = await self.client.request(
            "POST",
            f"{self._git}/pushes",
            json={
                "refUpdates": [{"name": f"refs/heads/{branch}", "oldObjectId": head}],
                "commits": [{"comment": message, "changes": changes}],
            },
        )
        commits = (pushed or {}).get("commits") or []
        return str(commits[0].get("commitId", "")) if commits else ""

    async def _content(self, path: str, version: str, kind: str) -> str | None:
        """A file's text at a branch or commit, `None` when it does not exist there."""
        try:
            item = await self.client.request(
                "GET",
                f"{self._git}/items",
                params={
                    "path": f"/{path.lstrip('/')}",
                    "includeContent": "true",
                    "versionDescriptor.version": version,
                    "versionDescriptor.versionType": kind,
                },
            )
        except UpstreamError as exc:
            if exc.status_code == 404:
                return None
            raise
        return str((item or {}).get("content") or "")

    async def list_files(self, repo: str, ref: str, directory: str) -> list[str]:
        """The files of a folder on a branch (one level). An absent folder has no files."""
        try:
            listed = await self.client.request(
                "GET",
                f"{self._git}/items",
                params={
                    "scopePath": f"/{directory.strip('/')}",
                    "recursionLevel": "OneLevel",
                    "versionDescriptor.version": ref,
                    "versionDescriptor.versionType": "branch",
                },
            )
        except UpstreamError as exc:
            if exc.status_code == 404:
                return []
            raise
        return sorted(
            str(i["path"]).lstrip("/")
            for i in (listed or {}).get("value", [])
            if not i.get("isFolder") and i.get("gitObjectType", "blob") == "blob"
        )

    # ───────────────────────── pull requests ─────────────────────────

    async def open_pr(self, repo: str, head: str, base: str, title: str, body: str, draft: bool) -> PrRef:
        existing = await self.client.request(
            "GET",
            f"{self._git}/pullrequests",
            params={
                "searchCriteria.sourceRefName": f"refs/heads/{head}",
                "searchCriteria.targetRefName": f"refs/heads/{base}",
                "searchCriteria.status": "active",
            },
        )
        found = (existing or {}).get("value") or []
        pr = (
            found[0]
            if found
            else await self.client.request(
                "POST",
                f"{self._git}/pullrequests",
                json={
                    "sourceRefName": f"refs/heads/{head}",
                    "targetRefName": f"refs/heads/{base}",
                    "title": title,
                    "description": body,
                    "isDraft": draft,
                },
            )
        )
        number = int(pr["pullRequestId"])
        return PrRef(repo=self.repository, number=number, url=self.web_url(number), head=head, base=base)

    async def update_pr(self, ref: PrRef, body: str | None, draft: bool | None) -> None:
        payload: dict[str, Any] = {}
        if body is not None:
            payload["description"] = body
        if draft is not None:
            payload["isDraft"] = draft
        if payload:
            await self.client.request("PATCH", self._pr(ref), json=payload)

    async def comment_pr(self, ref: PrRef, body: str) -> str:
        thread = await self.client.request(
            "POST",
            f"{self._pr(ref)}/threads",
            json={
                "comments": [{"parentCommentId": 0, "content": body, "commentType": "text"}],
                "status": "active",
            },
        )
        return f"{self.web_url(ref.number)}?discussionId={(thread or {}).get('id', '')}"

    async def get_pr(self, ref: PrRef) -> PrState:
        pr = await self.client.request("GET", self._pr(ref))
        head = str((pr.get("lastMergeSourceCommit") or {}).get("commitId") or "") or None
        source = str(pr.get("sourceRefName") or "").removeprefix("refs/heads/")
        iterations = await self.client.request("GET", f"{self._pr(ref)}/iterations")
        latest = max((int(i["id"]) for i in (iterations or {}).get("value", [])), default=0)
        checks = [
            *await self._statuses(ref, latest),
            *await self._policies(pr, ref),
            *await self._branch_builds(pr, source, head),
        ]
        labels = await self.client.request("GET", f"{self._pr(ref)}/labels")
        merge_status = str(pr.get("mergeStatus") or "")
        return PrState(
            ref=PrRef(
                repo=self.repository,
                number=ref.number,
                url=self.web_url(ref.number),
                head=source or None,
                base=str(pr.get("targetRefName") or "").removeprefix("refs/heads/") or None,
            ),
            title=str(pr.get("title", "")),
            body=pr.get("description") or "",
            draft=bool(pr.get("isDraft")),
            merged=pr.get("status") == "completed",
            mergeable={"succeeded": True, "conflicts": False}.get(merge_status),
            head_sha=head,
            checks=checks,
            reviews=[
                ReviewState(
                    reviewer=_reviewer_name(r),
                    state="approved" if r["vote"] in APPROVING_VOTES else "changes_requested",
                )
                for r in pr.get("reviewers") or []
                if not r.get("isContainer") and r.get("vote") in APPROVING_VOTES | REFUSING_VOTES
            ],
            files=await self._changed_files(ref, latest),
            labels=[str(label["name"]) for label in (labels or {}).get("value", []) if label.get("name")],
        )

    async def _statuses(self, ref: PrRef, latest_iteration: int) -> list[CheckRun]:
        """The pull request's statuses, the newest of each context, those of an older iteration dropped."""
        found = await self.client.request("GET", f"{self._pr(ref)}/statuses")
        newest: dict[str, dict[str, Any]] = {}
        for status in sorted((found or {}).get("value", []), key=lambda s: str(s.get("creationDate", ""))):
            iteration = status.get("iterationId")
            if iteration is not None and int(iteration) < latest_iteration:
                continue
            context = status.get("context") or {}
            newest[f"{context.get('genre', '')}/{context.get('name', '')}"] = status
        runs: list[CheckRun] = []
        for name, status in newest.items():
            progress, conclusion = _STATUS_CHECK.get(str(status.get("state")), ("in_progress", None))
            runs.append(
                CheckRun(
                    name=name.lstrip("/"),
                    status=progress,
                    conclusion=conclusion,
                    url=status.get("targetUrl"),
                    details=str(status.get("description") or ""),
                )
            )
        return runs

    async def _policies(self, pr: Mapping[str, Any], ref: PrRef) -> list[CheckRun]:
        """The Build and Status branch policies of the pull request: Azure blocks completion on them, so
        they are the checks CI must be green on. Other policies (reviewers, comments) are not CI."""
        project_id = str(((pr.get("repository") or {}).get("project") or {}).get("id") or "")
        if not project_id:
            return []
        found = await self.client.request(
            "GET",
            f"{self._scope}/policy/evaluations",
            params={
                "artifactId": f"vstfs:///CodeReview/CodeReviewId/{project_id}/{ref.number}",
                "api-version": "7.1-preview.1",
            },
        )
        runs: list[CheckRun] = []
        for evaluation in (found or {}).get("value", []):
            kind = ((evaluation.get("configuration") or {}).get("type") or {}).get("id")
            if kind not in {BUILD_POLICY, STATUS_POLICY}:
                continue
            name = ((evaluation.get("configuration") or {}).get("settings") or {}).get("displayName") or (
                (evaluation["configuration"].get("type") or {}).get("displayName", "policy")
            )
            progress, conclusion = _EVALUATION_CHECK.get(str(evaluation.get("status")), ("in_progress", None))
            runs.append(
                CheckRun(
                    name=f"policy: {name}",
                    status=progress,
                    conclusion=conclusion,
                )
            )
        return runs

    async def _branch_builds(self, pr: Mapping[str, Any], source: str, head: str | None) -> list[CheckRun]:
        """The builds of the source branch at the head commit, the latest of each pipeline: the CI a
        push triggers, which no branch policy lists."""
        repo_id = str((pr.get("repository") or {}).get("id") or "")
        if not (repo_id and source and head):
            return []
        found = await self.client.request(
            "GET",
            f"{self._scope}/build/builds",
            params={
                "repositoryId": repo_id,
                "repositoryType": "TfsGit",
                "branchName": f"refs/heads/{source}",
                "queryOrder": "queueTimeDescending",
                "$top": _BUILDS_READ,
            },
        )
        builds = [b for b in (found or {}).get("value", []) if source_commit_of(b) == head]
        return [
            check_of(
                build_name_of(b),
                str(b.get("status")),
                b.get("result"),
                partial=self.partial,
                url=web_url_of(b),
            )
            for b in latest_per_pipeline(builds)
        ]

    async def _changed_files(self, ref: PrRef, iteration: int) -> list[str]:
        if not iteration:
            return []
        paths: list[str] = []
        skip = 0
        for _ in range(_MAX_CHANGE_PAGES):
            page = await self.client.request(
                "GET",
                f"{self._pr(ref)}/iterations/{iteration}/changes",
                params={"$top": _PAGE, "$skip": skip},
            )
            entries = (page or {}).get("changeEntries") or []
            paths.extend(
                str((e.get("item") or {}).get("path", "")).lstrip("/")
                for e in entries
                if not (e.get("item") or {}).get("isFolder")
            )
            skip = int((page or {}).get("nextSkip") or 0)
            if not skip:
                break
        return paths

    async def request_review(self, ref: PrRef, reviewers: list[str]) -> None:
        """Ask people to review. A reviewer is an identity id, or a sign-in name / e-mail resolved through
        the identities API; a team (`org/team`) and a name no identity answers to are left out."""
        for name in reviewers:
            if "/" in name:
                continue
            identity = name if _is_guid(name) else await self._identity_id(name)
            if identity is None:
                log.warning("azure_repos_reviewer_unknown", reviewer=name)
                continue
            await self.client.request(
                "PUT", f"{self._pr(ref)}/reviewers/{identity}", json={"vote": 0, "isRequired": False}
            )

    async def _identity_id(self, name: str) -> str | None:
        parts = urlsplit(self.client.base_url)
        base = (
            f"https://vssps.dev.azure.com/{parts.path.strip('/').split('/')[0]}"
            if (parts.hostname or "") == "dev.azure.com"
            else self.client.base_url
        )
        found = await self.client.request(
            "GET",
            f"{base}/_apis/identities",
            params={"searchFilter": "General", "filterValue": name, "queryMembership": "None"},
        )
        wanted = name.casefold()
        candidates = [i for i in (found or {}).get("value", []) if i.get("id")]
        exact = [
            i
            for i in candidates
            if wanted
            in {
                str((i.get("properties") or {}).get("Account", {}).get("$value", "")).casefold(),
                str((i.get("properties") or {}).get("Mail", {}).get("$value", "")).casefold(),
                str(i.get("providerDisplayName", "")).casefold(),
            }
        ]
        # The search is fuzzy: only an exact account, e-mail or display name is a reviewer, never a near miss.
        return str(exact[0]["id"]) if len(exact) == 1 else None

    async def enqueue_merge(self, ref: PrRef, sha: str) -> None:
        """Complete the pull request at `sha`, the commit the gates judged (ADR 0171).

        A head that is elsewhere is a `HeadMovedError` and nothing is completed; a pull request already
        completed at `sha` is left as it is, so a replayed merge is not a second one. Auto-complete is not
        set, no policy is bypassed: a policy that is not met makes Azure refuse, and the refusal is raised."""
        pr = await self.client.request("GET", self._pr(ref))
        head = str((pr.get("lastMergeSourceCommit") or {}).get("commitId") or "")
        if head != sha:
            raise HeadMovedError("azure-repos", expected=sha, actual=head or None)
        if pr.get("status") == "completed":
            return
        try:
            await self.client.request(
                "PATCH",
                self._pr(ref),
                json={
                    "status": "completed",
                    "lastMergeSourceCommit": {"commitId": sha},
                    "completionOptions": {
                        "mergeStrategy": self.merge_strategy,
                        "deleteSourceBranch": self.delete_source_branch,
                        "bypassPolicy": False,
                    },
                },
            )
        except UpstreamError as exc:
            if exc.status_code == 409:  # the source branch moved between the read and the completion
                raise HeadMovedError("azure-repos", expected=sha) from exc
            raise
        await self._await_completion(ref)

    async def _await_completion(self, ref: PrRef) -> None:
        """Completion is asynchronous: the merge is queued, then done. Poll a few times; one still not done
        is left (a replay finds it completed), one that conflicted or was refused is an error."""
        for attempt in range(self.completion_polls):
            pr = await self.client.request("GET", self._pr(ref))
            if pr.get("status") == "completed":
                return
            if str(pr.get("mergeStatus") or "") in {"conflicts", "rejectedByPolicy", "failure"}:
                raise UpstreamError(
                    "azure-repos",
                    f"pull request {ref.number} was not completed: merge status {pr.get('mergeStatus')}",
                )
            if attempt + 1 < self.completion_polls:
                await self._sleep(1.0)

    # ───────────────────────── statuses ─────────────────────────

    async def create_check_run(
        self,
        repo: str,
        sha: str,
        name: str,
        conclusion: str,
        summary: str,
        annotations: list[str] | None = None,
    ) -> None:
        """A commit status `choregos/<name>`: the verdict of a guarantee, visible on the pull request.
        Azure statuses carry no annotations, so the files are named in the description."""
        description = summary
        if annotations:
            description += " (outside the perimeter: " + ", ".join(annotations[:10]) + ")"
        await self.client.request(
            "POST",
            f"{self._git}/commits/{segment(sha)}/statuses",
            json={
                "state": _COMMIT_STATE.get(conclusion, "error"),
                "description": description[:400],
                "context": {"name": name, "genre": "choregos"},
            },
        )

    # ───────────────────────── compare ─────────────────────────

    async def compare(self, repo: str, base: str, head: str) -> DiffSummary:
        """The files `head` (a branch or a commit) changes against `base`, with line counts and a
        unified patch computed from the two versions of each text file: Azure's diff lists the changed
        paths only. More than `diff_files_max` files, or a file over `diff_file_bytes_max`, is listed with no
        counts (never silently dropped)."""
        found = await self.client.request(
            "GET",
            f"{self._git}/diffs/commits",
            params={
                "baseVersion": base,
                "baseVersionType": _version_type(base),
                "targetVersion": head,
                "targetVersionType": _version_type(head),
                "$top": _PAGE,
            },
        )
        files: list[DiffFile] = []
        for position, change in enumerate((found or {}).get("changes", [])):
            item = change.get("item") or {}
            if item.get("isFolder"):
                continue
            kind = str(change.get("changeType", "edit"))
            path = str(item.get("path", "")).lstrip("/")
            old = str(change.get("sourceServerItem") or item.get("path", "")).lstrip("/")
            status = next((s for k, s in _CHANGE_STATUS.items() if k in kind), "modified")
            additions = deletions = 0
            patch: str | None = None
            if position < self.diff_files_max:
                additions, deletions, patch = await self._line_counts(base, head, old, path, status)
            files.append(
                DiffFile(
                    path=path,
                    status=status,
                    additions=additions,
                    deletions=deletions,
                    patch=patch,
                    old_path=old if status == "renamed" else None,
                )
            )
        return DiffSummary(base=base, head=head, files=files)

    async def _line_counts(
        self, base: str, head: str, old: str, new: str, status: str
    ) -> tuple[int, int, str | None]:
        before = "" if status == "added" else await self._content(old, base, _version_type(base))
        after = "" if status == "removed" else await self._content(new, head, _version_type(head))
        if before is None or after is None:
            return 0, 0, None
        if max(len(before), len(after)) > self.diff_file_bytes_max:
            return 0, 0, None
        lines = list(
            difflib.unified_diff(
                before.splitlines(), after.splitlines(), f"a/{old}", f"b/{new}", lineterm="", n=3
            )
        )
        added = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
        return added, removed, "\n".join(lines[2:])

    # ───────────────────────── inbound ─────────────────────────

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> list[InboundEvent]:
        """The pull request service hooks: `git.pullrequest.created`, `.updated`, `.merged`."""
        events = parse_azure_devops_event("", json.loads(body or b"{}"))
        return [e for e in events if str(e.type).startswith("scm.")]

    async def test(self) -> dict[str, Any]:
        repo = await self.client.request("GET", self._git)
        return {
            "ok": bool(repo),
            "repository": self.repository,
            "default_branch": str((repo or {}).get("defaultBranch") or "").removeprefix("refs/heads/")
            or None,
        }


def _version_type(version: str) -> str:
    return "commit" if SHA.match(version) else "branch"


def _is_guid(value: str) -> bool:
    import re

    return bool(re.fullmatch(r"[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}", value))


def _reviewer_name(reviewer: Mapping[str, Any]) -> str:
    return str(reviewer.get("uniqueName") or reviewer.get("displayName") or reviewer.get("id") or "")
