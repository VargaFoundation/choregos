# SPDX-License-Identifier: Apache-2.0
"""Normalization of Azure DevOps service hooks of Repos and Pipelines into `InboundEvent` (S13-08).

A service hook names its event in `eventType`: `git.pullrequest.created`, `git.pullrequest.updated`,
`git.pullrequest.merged`, `build.complete`, `ms.vss-pipelines.run-state-changed-event`. The payloads are
written here from Microsoft's service-hook events reference, not captured from an organisation: where the
reference is silent on a field (where the commit of a run-state event sits), several places are read.

A delivery's `project_slug` is the lower-cased team project, as the Boards route's is without a
connector; the API routes a delivery to the project whose Azure Repos or Pipelines connector holds that
team project. The work item is `choregos/<id>` in the branch name, the id of the Azure Boards work item.
"""

from __future__ import annotations

import re
from typing import Any

from tranibor_contracts import InboundEvent, InboundEventType

SOURCE = "azure-devops"
BRANCH_KEY_RE = re.compile(r"choregos/(?P<number>\d+)")
_REF = "refs/heads/"


def parse_azure_devops_event(delivery_id: str, payload: dict[str, Any]) -> list[InboundEvent]:
    """A service-hook delivery as zero, one or several events; an event this does not know is none."""
    handler = _HANDLERS.get(str(payload.get("eventType") or ""))
    if handler is None:
        return []
    resource = payload.get("resource")
    if not isinstance(resource, dict):
        return []
    return handler(
        delivery_id or str(payload.get("notificationId") or payload.get("id") or ""), payload, resource
    )


def branch_of(ref: Any) -> str:
    return str(ref or "").removeprefix(_REF)


def work_item_of(branch: str) -> str | None:
    match = BRANCH_KEY_RE.search(branch or "")
    return match.group("number") if match else None


def _at(obj: Any, *path: str) -> Any:
    """`obj[path[0]][path[1]]...`, `None` as soon as a step is not an object."""
    for step in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(step)
    return obj


def team_project_of(payload: dict[str, Any]) -> str | None:
    """The team project a delivery is about, wherever the event type keeps it."""
    resource = payload.get("resource")
    candidates = [
        _at(resource, "repository", "project", "name"),
        _at(resource, "project", "name"),
        _at(resource, "run", "project", "name"),
        _at(payload, "resourceContainers", "project", "name"),
    ]
    return next((str(c) for c in candidates if c), None)


def repository_name_of(payload: dict[str, Any]) -> str | None:
    resource = payload.get("resource")
    name = _at(resource, "repository", "name")
    if not name:
        repositories = _at(resource, "repositories")
        first = repositories[0] if isinstance(repositories, list) and repositories else None
        name = _at(first, "repository", "name")
    return str(name) if name else None


def _common(delivery_id: str, payload: dict[str, Any], key: str | None, actor: Any = None) -> dict[str, Any]:
    team = team_project_of(payload)
    return {
        "source": SOURCE,
        "delivery_id": delivery_id,
        "project_slug": team.casefold() if team else None,
        "work_item_key": key,
        "actor": str(actor) if actor else None,
    }


def _web(resource: dict[str, Any]) -> str | None:
    web = ((resource.get("_links") or {}).get("web") or {}).get("href")
    return str(web) if web else None


def _pull_request(delivery_id: str, payload: dict[str, Any], resource: dict[str, Any]) -> list[InboundEvent]:
    head = branch_of(resource.get("sourceRefName"))
    common = _common(
        delivery_id,
        payload,
        work_item_of(head),
        (resource.get("createdBy") or {}).get("uniqueName"),
    )
    status = str(resource.get("status") or "")
    body = {
        "number": resource.get("pullRequestId"),
        "pr_url": _pr_url(payload, resource),
        "sha": (resource.get("lastMergeSourceCommit") or {}).get("commitId"),
        "base": branch_of(resource.get("targetRefName")),
        "head": head,
        "draft": bool(resource.get("isDraft")),
        "labels": [str(label.get("name")) for label in resource.get("labels") or [] if label.get("name")],
        "merged": status == "completed",
    }
    kind = str(payload.get("eventType"))
    if kind == "git.pullrequest.created":
        return [InboundEvent(type=InboundEventType.PR_OPENED, payload=body, **common)]
    if status == "completed":
        return [InboundEvent(type=InboundEventType.PR_MERGED, payload=body, **common)]
    if status == "abandoned":
        return [InboundEvent(type=InboundEventType.PR_CLOSED, payload=body, **common)]
    if kind == "git.pullrequest.updated":
        # Any change of an active pull request (a push, a vote, a status) arrives as `updated`: the gates
        # read the pull request again, they never trust what the delivery says of a vote.
        return [InboundEvent(type=InboundEventType.PR_SYNCHRONIZED, payload=body, **common)]
    return []  # `git.pullrequest.merged` of an active pull request is a merge attempt, not a merge


def _pr_url(payload: dict[str, Any], resource: dict[str, Any]) -> str | None:
    repository = resource.get("repository") or {}
    base = str(((payload.get("resourceContainers") or {}).get("collection") or {}).get("baseUrl") or "")
    project = (repository.get("project") or {}).get("name")
    name = repository.get("name")
    if not (base and project and name and resource.get("pullRequestId")):
        return None
    return f"{base.rstrip('/')}/{project}/_git/{name}/pullrequest/{resource['pullRequestId']}"


_BUILD = {
    "succeeded": InboundEventType.CI_SUCCEEDED,
    "failed": InboundEventType.CI_FAILED,
    "partiallysucceeded": InboundEventType.CI_FAILED,
    "canceled": InboundEventType.CI_FAILED,
}


def _build(delivery_id: str, payload: dict[str, Any], resource: dict[str, Any]) -> list[InboundEvent]:
    result = str(resource.get("result") or "").lower()
    kind = _BUILD.get(result)
    if kind is None:
        return []
    branch = branch_of(resource.get("sourceBranch"))
    common = _common(
        delivery_id, payload, work_item_of(branch), (resource.get("requestedFor") or {}).get("uniqueName")
    )
    return [
        InboundEvent(
            type=kind,
            payload={
                "conclusion": result,
                "sha": resource.get("sourceVersion"),
                "branch": branch,
                "url": _web(resource),
                "pipeline_id": (resource.get("definition") or {}).get("id"),
                "run_id": resource.get("id"),
            },
            **common,
        )
    ]


_RUN_RESULT = {
    "succeeded": InboundEventType.CI_SUCCEEDED,
    "failed": InboundEventType.CI_FAILED,
    "canceled": InboundEventType.CI_FAILED,
}


def _run_state(delivery_id: str, payload: dict[str, Any], resource: dict[str, Any]) -> list[InboundEvent]:
    run = resource.get("run") or {}
    state = str(run.get("state") or "")
    result = str(run.get("result") or "").lower()
    repositories = resource.get("repositories")
    first = (
        repositories[0]
        if isinstance(repositories, list) and repositories and isinstance(repositories[0], dict)
        else {}
    )
    branch = branch_of(first.get("refName"))
    sha = first.get("version") or (first.get("change") or {}).get("version")
    if state == "completed":
        kind = _RUN_RESULT.get(result)
        if kind is None:
            return []
    elif state == "inProgress":
        kind = InboundEventType.CI_STARTED
    else:
        return []
    common = _common(delivery_id, payload, work_item_of(branch))
    return [
        InboundEvent(
            type=kind,
            payload={
                "conclusion": result or state.lower(),
                "sha": sha,
                "branch": branch,
                "url": _web(run),
                "pipeline_id": (run.get("pipeline") or resource.get("pipeline") or {}).get("id"),
                "run_id": run.get("id"),
            },
            **common,
        )
    ]


_HANDLERS = {
    "git.pullrequest.created": _pull_request,
    "git.pullrequest.updated": _pull_request,
    "git.pullrequest.merged": _pull_request,
    "build.complete": _build,
    "ms.vss-pipelines.run-state-changed-event": _run_state,
}
