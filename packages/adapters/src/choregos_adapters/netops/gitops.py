# SPDX-License-Identifier: Apache-2.0
"""A netops change as a pull request to the GitOps repository (ADR 0052, S33-09).

A Cilium policy or an nginx configuration is never applied by the platform: it is written on a
branch of the GitOps repository and offered as a pull request, through the SCM adapter (the
platform holds the forge's credentials; the agent never does). What runs in production is what the
release train carries after the merge (ADR 0006), and what Argo CD syncs from the repository
(ADR 0009).

The branch says which work item the change belongs to. When the action comes from a work item of
the GitOps repository itself, the branch is `choregos/<number>-netops-…`: the forge's webhook maps
the merged pull request back to that work item (`github_events._work_item_from_branch`), and the
webhook boards the train the work item's workflow takes. Otherwise the branch is `netops/<key>`
and the pull request is merged by the repository's own process.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..errors import AdapterError

MAX_FILE_BYTES = 256 * 1024
_ISSUE = re.compile(r"^(?P<repo>[^#\s]+/[^#\s]+)#(?P<number>\d+)$")


@dataclass(frozen=True)
class GitOpsTarget:
    """Where changes are proposed: a repository, its base branch, the root the connector owns."""

    scm: Any
    repo: str
    base: str = "main"
    root: str = ""


def branch_for(repo: str, action_key: str, work_item_key: str | None) -> str:
    match = _ISSUE.match(work_item_key or "")
    if match and match.group("repo") == repo:
        return f"choregos/{match.group('number')}-netops-{action_key[:8]}"
    return f"netops/{action_key[:12]}"


async def propose_change(
    target: GitOpsTarget,
    *,
    files: dict[str, str],
    title: str,
    rationale: str,
    action_key: str,
    work_item_key: str | None,
) -> dict[str, Any]:
    """Branch, commit, pull request — each idempotent (an existing branch is kept, an identical
    file is not rewritten, an open pull request on the branch is reused): a replayed action opens
    nothing twice. Never merged here."""
    for path, content in files.items():
        if len(content.encode()) > MAX_FILE_BYTES:
            raise AdapterError(f"{path} is larger than {MAX_FILE_BYTES // 1024} KiB")
    branch = branch_for(target.repo, action_key, work_item_key)
    await target.scm.ensure_branch(target.repo, branch, target.base)
    commit = await target.scm.commit_files(target.repo, branch, files, title)
    body = (
        f"{rationale}\n\n"
        f"Proposed by a governed action of Choregos (key `{action_key}`)"
        + (f" for {work_item_key}" if work_item_key else "")
        + ". Merge it through the repository's review; production follows the release train."
    )
    pr = await target.scm.open_pr(target.repo, branch, target.base, title, body, False)
    return {
        "repo": target.repo,
        "branch": branch,
        "pr": pr.number,
        "url": pr.url,
        "commit": commit,
        "files": sorted(files),
        "work_item": work_item_key,
    }
