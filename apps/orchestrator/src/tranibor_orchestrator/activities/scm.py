# SPDX-License-Identifier: Apache-2.0
"""SCM activities: branch, PR, review, merge queue, driven by the `system` transitions."""

from __future__ import annotations

import json
from typing import Any

import structlog
from temporalio import activity
from temporalio.exceptions import ApplicationError
from tranibor_adapters.base import merge_at_commit_refusal
from tranibor_adapters.errors import HeadMovedError
from tranibor_api.db.models import Run
from tranibor_contracts import StageResult
from tranibor_core.domain import PrRef
from tranibor_core.dsl.flow import PULL_REQUEST_SECTIONS
from tranibor_core.restricted import WITHHELD, is_marked

from .base import console_base, db, load_work_item, project_bundle, scm_for
from .judged import judged_commit
from .outcome import record_the_diff
from .repositories import fix, repo_slug, target_of, target_or_none

log = structlog.get_logger(__name__)

#: The error type `open_pull_request` raises when the work item has no branch to open a pull request
#: from (S22-46): the implementation changed nothing, so nothing was pushed. Non-retryable — a retry
#: finds the same absent branch — and the interpreter escalates the work item on it.
NO_BRANCH = "NoBranch"
#: The error type `enqueue_merge` raises when the pull request's head is not the commit the merge
#: names (ADR 0171): nothing was merged. Non-retryable — the same merge would be refused the same
#: way — so the work item's workflow stops on it with this reason, and its gates judge the new head
#: when it is started again.
HEAD_MOVED = "HeadMoved"
#: The error type `enqueue_merge` raises when the merge cannot name a commit (ADR 0171): the SCM
#: connector declares no `merge_at_commit`, or the forge gives the pull request no head. The platform
#: does not merge a head it cannot name. Non-retryable.
MERGE_UNPINNED = "MergeUnpinned"


def _target(bundle: Any, item: Any) -> Any:
    """The work item's target repository (ADR 0124), or an error that names the cause.

    These activities assume a repository: opening a PR, pushing a branch, reading checks.
    A project without a repository (ADR 0012) simply has no transition that triggers them, but if
    one did, better to fail here, saying why, than three layers further down on an empty URL. A target
    the project does not serve is a non-retryable `RepositoryRefused` (`target_of`).
    """
    found = target_of(bundle, item)
    if found is None:
        raise ValueError(
            f"the project {bundle.slug} has no repository: this step needs an SCM "
            "(pull request, branch, checks). Remove the transition, or declare a repository."
        )
    return found


@activity.defn(name="open_pull_request")
async def open_pull_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Open (or find) the work item's PR and update its body; idempotent."""
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        item = await load_work_item(session, payload["work_item_id"])
        found = _target(bundle, item)
        scm = await scm_for(session, bundle, found.repo.url)
        repo = repo_slug(found.repo.url)
        branch = bundle.config.branch_for(item.tracker_key, repo=found.repo)
        base = found.repo.default_branch
        fix(bundle, item, found)
        documents = item.documents or {}
        # A marked work item's documents stay in the console; the code is still delivered (ADR 0114, C).
        body = (
            _withheld_pr_body(item, bundle.slug, await console_base(bundle))
            if is_marked(item)
            else _pr_body(item, documents)
        )
        # Without a pushed branch GitHub answers 422 `head invalid`, retried then fatal (#539, on
        # sandbox-dev#21). Refused here instead, with a reason a person can read. Any error reading
        # the branch (403, 5xx) is raised as it is, and retried as before.
        if not await scm.branch_exists(repo, branch):
            raise ApplicationError(
                f"no branch: the implementation changed nothing — `{branch}` was never pushed to "
                f"{repo}, so there is no pull request to open",
                type=NO_BRANCH,
                non_retryable=True,
            )
        # `pull_request: {draft: true}` on the transition (S51-11): the pull request is born a draft,
        # and one already open on the branch keeps its state (a person may have marked it ready).
        # Without it, as before: ready for review, and marked so.
        draft = bool(payload.get("draft", False))
        ref = await scm.open_pr(repo, branch, base, item.title, body, draft=draft)
        await scm.update_pr(ref, body=body, draft=None if draft else False)
        item.pr_url = ref.url or item.pr_url
        reviewers = payload.get("reviewers") or []
        if reviewers:
            await scm.request_review(ref, list(reviewers))
        return {"pr_url": item.pr_url, "number": ref.number}


def _withheld_pr_body(item: Any, project_slug: str, console: str) -> str:
    """The body of a marked work item's pull request: its key and the link, nothing it read."""
    link = f"{console}/p/{project_slug}/items/{item.id}"
    return "\n".join([f"Work item: {item.tracker_key}", "", WITHHELD, "", link])


def _pr_body(item: Any, documents: dict[str, Any]) -> str:
    """The PR body, in English (ADR 0039): GitHub shows it. The release notes that a `release_notes`
    agent wrote appear in it; they were read nowhere (S21-22)."""
    parts = [f"Work item: {item.tracker_key}"]
    for name, title in PULL_REQUEST_SECTIONS:
        if title is None:
            # The bare text that heads the body; the work item's own body stands in for a missing spec.
            parts += ["", documents.get(name, item.body_snapshot or "")]
        elif documents.get(name):
            parts += ["", f"## {title}", documents[name]]
    parts += ["", "---", "_Opened by Tranibor; the cost and the evidence are on the work item._"]
    return "\n".join(parts)


@activity.defn(name="enqueue_merge")
async def enqueue_merge(payload: dict[str, Any]) -> dict[str, Any]:
    """Merge the work item's pull request at the commit its gates judged (ADR 0171).

    The merge names that commit, and the forge refuses it when the pull request's head is elsewhere:
    a typed, non-retryable `HeadMoved`, nothing merged, never a second try without the commit. A
    connector that cannot name a commit is not asked to merge (`MergeUnpinned`). Replayable (ADR
    0008): a pull request already merged at that commit is left as it is.
    """
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        item = await load_work_item(session, payload["work_item_id"])
        if not item.pr_url:
            return {"enqueued": False, "reason": "no pull request"}
        found = _target(bundle, item)
        number = int(item.pr_url.rsplit("/", 1)[-1])
        scm = await scm_for(session, bundle, found.repo.url)
        refusal = merge_at_commit_refusal(scm)
        if refusal is not None:
            raise ApplicationError(refusal, type=MERGE_UNPINNED, non_retryable=True)
        repo = repo_slug(found.repo.url)
        ref = PrRef(repo=repo, number=number)
        sha = judged_commit(item, repo)
        named = "its gates judged"
        if sha is None:
            # No gate ruled in the state the work item merges from: its merge transition carries
            # none, so no verdict is betrayed. The head is read once, here, and the merge names it.
            sha = (await scm.get_pr(ref)).head_sha
            named = "the merge read"
        if not sha:
            raise ApplicationError(
                f"the pull request {item.pr_url} has no head commit the merge could name: nothing was merged",
                type=MERGE_UNPINNED,
                non_retryable=True,
            )
        try:
            await scm.enqueue_merge(ref, sha)
        except HeadMovedError as moved:
            log.warning(
                "merge_refused_head_moved",
                project=bundle.slug,
                work_item=item.tracker_key,
                expected=sha,
                head=moved.actual,
            )
            now = f"is now at {moved.actual[:12]}" if moved.actual else "has moved"
            raise ApplicationError(
                f"the pull request {item.pr_url} moved: {named} {sha[:12]} and its head {now}. Nothing "
                "was merged. Start the work item again (`mark_agent_ready`): its gates will judge the "
                "new head",
                type=HEAD_MOVED,
                non_retryable=True,
            ) from moved
        return {"enqueued": True, "pr_url": item.pr_url, "sha": sha}


@activity.defn(name="ensure_branch")
async def ensure_branch(payload: dict[str, Any]) -> dict[str, str]:
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        item = await load_work_item(session, payload["work_item_id"])
        found = _target(bundle, item)
        branch = bundle.config.branch_for(item.tracker_key, repo=found.repo)
        scm = await scm_for(session, bundle, found.repo.url)
        await scm.ensure_branch(repo_slug(found.repo.url), branch, found.repo.default_branch)
        fix(bundle, item, found)
        return {"branch": branch}


@activity.defn(name="collect_run_artifacts")
async def collect_run_artifacts(payload: dict[str, Any]) -> dict[str, Any]:
    """File the run's diff in its result: it is what the front end shows."""
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        item = await load_work_item(session, payload["work_item_id"])
        run = await session.get(Run, payload["run_id"])
        if run is None or not run.result:
            return {"files": 0}
        found = target_or_none(bundle, item)
        if found is None:
            # A project without a repository: there is no diff to file, and that is not an
            # error. This activity is called after EVERY agent step, including those of a trade that
            # writes no code file; failing killed the work item right after a successful step. Seen on
            # the bench of 2026-09-24: the HR sourcing succeeded, then the workflow died on "no repository".
            # A target the project does not serve has no diff either; the next stage says why.
            return {"files": 0}
        repo = repo_slug(found.repo.url)
        branch = bundle.config.branch_for(item.tracker_key, repo=found.repo)
        try:
            scm = await scm_for(session, bundle, found.repo.url)
            # The diff of the commit at the head of the branch, read once (ADR 0171); a branch that
            # is not there is compared by its name, and the SCM answers as it did.
            head_of = getattr(scm, "head_of", None)
            head = await head_of(repo, branch) if head_of is not None else None
            diff = await scm.compare(repo, found.repo.default_branch, head or branch)
        except Exception:
            return {"files": 0}
        result = StageResult.model_validate(run.result)
        result.artifacts.reports["diff_files"] = json.dumps(
            [
                {"path": f.path, "status": f.status, "additions": f.additions, "deletions": f.deletions}
                for f in diff.files
            ]
        )
        result.artifacts.reports["base"] = found.repo.default_branch
        result.evidence.diff_files = len(diff.files)
        result.evidence.diff_lines = diff.additions + diff.deletions
        run.result = result.model_dump(mode="json", by_alias=True)
        await record_the_diff(session, item, run, diff)
        return {"files": len(diff.files)}
