# SPDX-License-Identifier: Apache-2.0
"""The Azure Pipelines CiAdapter: builds of a commit, run state and logs, queueing, service hooks (S13-08).

Azure DevOps keeps one thing behind two APIs: a *run* of the Pipelines API and a *build* of the Build API
share their id, so a run id is read, logged and cancelled as a build. A build has a `status` (where it is)
and a `result` (how it ended); `status_for` reads the builds whose commit is the one asked, the latest of
each pipeline, and rules like this:

* `succeeded` is green; `failed` is red; `canceled` is an error, never a green;
* `partiallySucceeded` (a task set to continue on error failed) is **red** unless the connector says
  `partially_succeeded: neutral`: a governed merge does not take warnings for a pass;
* a build that is not completed is pending or running, never green.

A commit with no build is `unknown`. Only the 100 most recent builds of the repository (or of the pipeline
asked) are read, so a commit older than them is `unknown` too.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal

from tranibor_contracts import InboundEvent
from tranibor_core.domain import CheckRun, CiStatus

from ..azure_devops import SHA, segment
from ..azure_devops_events import parse_azure_devops_event
from ..errors import ConfigurationError, UpstreamError
from ..http import RestClient

State = Literal["pending", "running", "success", "failure", "error", "unknown"]
Progress = Literal["queued", "in_progress", "completed"]
Conclusion = Literal["success", "failure", "neutral", "cancelled", "timed_out", "skipped"]

_BUILDS_READ = 100
_MAX_LOG_FILES = 100
_STATUS_PROGRESS: dict[str, Progress] = {
    "notStarted": "queued",
    "postponed": "queued",
    "none": "queued",
    "inProgress": "in_progress",
    "cancelling": "in_progress",
}
_RESULT: dict[str, Conclusion] = {
    "succeeded": "success",
    "failed": "failure",
    "canceled": "cancelled",
    "skipped": "skipped",
    "abandoned": "cancelled",
}


def check_of(
    name: str, status: str, result: str | None, *, partial: Conclusion = "failure", url: str | None = None
) -> CheckRun:
    """One build, job or stage as a check: where it is, and how it ended once it has."""
    if status != "completed":
        return CheckRun(name=name, status=_STATUS_PROGRESS.get(status, "in_progress"), url=url)
    if result in {"partiallySucceeded", "succeededWithIssues"}:
        conclusion: Conclusion = partial
    else:
        # A completed build whose result is unreadable is not a pass.
        conclusion = _RESULT.get(str(result), "failure")
    return CheckRun(name=name, status="completed", conclusion=conclusion, url=url)


def state_of(checks: list[CheckRun]) -> State:
    """The state of the latest build of each pipeline together: a failure outranks what still runs."""
    if not checks:
        return "unknown"
    if any(c.status == "completed" and c.conclusion in {"failure", "timed_out"} for c in checks):
        return "failure"
    if any(c.status == "in_progress" for c in checks):
        return "running"
    if any(c.status == "queued" for c in checks):
        return "pending"
    if any(c.conclusion == "cancelled" for c in checks):
        return "error"
    return "success"


def source_commit_of(build: Mapping[str, Any]) -> str:
    """The commit a build was asked to build: for a build of a pull request's merge, the pull request's own
    source commit, since `sourceVersion` is then the merge commit Azure made."""
    info = build.get("triggerInfo") or {}
    pr = info.get("pr.sourceCommitId") or info.get("pr.sourceSha")
    return str(pr or build.get("sourceVersion") or "")


def build_name_of(build: Mapping[str, Any]) -> str:
    """`<pipeline> #<build number>`: what a check of a build is called."""
    pipeline = (build.get("definition") or {}).get("name", "pipeline")
    return f"{pipeline} #{build.get('buildNumber') or build.get('id')}"


def web_url_of(build: Mapping[str, Any]) -> str | None:
    web = ((build.get("_links") or {}).get("web") or {}).get("href")
    return str(web) if web else None


def latest_per_pipeline(builds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Newest first in, the first of each pipeline out."""
    seen: set[Any] = set()
    out: list[dict[str, Any]] = []
    for build in builds:
        key = (build.get("definition") or {}).get("id")
        if key in seen:
            continue
        seen.add(key)
        out.append(build)
    return out


class AzurePipelinesCi:
    """CI on one Azure DevOps team project. `repository` narrows the builds to one Azure Repos repository,
    `pipeline` (an id or a name) to one pipeline and is the one `trigger` queues without a name."""

    def __init__(
        self,
        client: RestClient,
        project: str,
        *,
        repository: str = "",
        pipeline: str = "",
        site_url: str | None = None,
        partially_succeeded: Conclusion = "failure",
    ) -> None:
        if not project:
            raise ConfigurationError("ci/azure-pipelines: `project` is required")
        self.client = client
        self.project = project
        self.repository = repository
        self.pipeline = pipeline
        self.site_url = (site_url or client.base_url).rstrip("/")
        self.partial: Conclusion = "neutral" if partially_succeeded == "neutral" else "failure"
        self._repository_id: str | None = None
        self._pipelines: dict[str, str] | None = None

    @property
    def _scope(self) -> str:
        return f"/{segment(self.project)}/_apis"

    # ───────────────────────── reading ─────────────────────────

    async def status_for(self, repo: str, sha: str) -> CiStatus:
        params: dict[str, Any] = {
            "queryOrder": "queueTimeDescending",
            "$top": _BUILDS_READ,
            "repositoryType": "TfsGit",
        }
        if self.repository:
            params["repositoryId"] = await self._repo_id()
        if self.pipeline:
            params["definitions"] = await self._pipeline_id(self.pipeline)
        found = await self.client.request("GET", f"{self._scope}/build/builds", params=params)
        builds = [b for b in (found or {}).get("value", []) if source_commit_of(b) == sha]
        latest = latest_per_pipeline(builds)
        if not latest:
            return CiStatus(state="unknown", sha=sha)
        runs = [await self._run_check(build) for build in latest]
        url = web_url_of(next((b for b in latest if self._is_red(b)), latest[0]))
        return CiStatus(state=state_of(runs), runs=runs, url=url, sha=sha)

    def _is_red(self, build: Mapping[str, Any]) -> bool:
        check = check_of("", str(build.get("status")), build.get("result"), partial=self.partial)
        return check.conclusion in {"failure", "timed_out"}

    async def _run_check(self, build: Mapping[str, Any]) -> CheckRun:
        name = build_name_of(build)
        return check_of(
            name, str(build.get("status")), build.get("result"), partial=self.partial, url=web_url_of(build)
        )

    async def run_status(self, run_ref: str) -> CiStatus:
        """One run, by its id: its state and result, and a check per job read from its timeline."""
        build = await self.client.request("GET", f"{self._scope}/build/builds/{segment(run_ref)}")
        check = await self._run_check(build)
        runs = [check, *await self._jobs(run_ref)]
        return CiStatus(
            state=state_of([check]), runs=runs, url=web_url_of(build), sha=source_commit_of(build) or None
        )

    async def _jobs(self, run_ref: str) -> list[CheckRun]:
        try:
            timeline = await self.client.request(
                "GET", f"{self._scope}/build/builds/{segment(run_ref)}/timeline"
            )
        except UpstreamError as exc:
            if exc.status_code == 404:  # a run that has not started has no timeline
                return []
            raise
        return [
            check_of(
                str(record.get("name", "")),
                _timeline_status(record),
                record.get("result"),
                partial=self.partial,
            )
            for record in (timeline or {}).get("records", [])
            if record.get("type") == "Job"
        ]

    async def logs(self, run_ref: str, tail: int = 500) -> str:
        """`run_ref` is a run (build) id: the logs of its steps, in order, as one text."""
        listing = await self.client.request("GET", f"{self._scope}/build/builds/{segment(run_ref)}/logs")
        entries = sorted((listing or {}).get("value", []), key=lambda e: int(e.get("id", 0)))
        chunks: list[str] = []
        for entry in entries[-_MAX_LOG_FILES:]:
            text = await self.client.request(
                "GET",
                f"{self._scope}/build/builds/{segment(run_ref)}/logs/{entry['id']}",
                headers={"Accept": "text/plain"},
                as_text=True,
            )
            chunks.append(str(text or ""))
        return "\n".join("\n".join(chunks).splitlines()[-tail:])

    def logs_url(self, run_ref: str) -> str:
        """The page of a run's logs, for a person."""
        return f"{self.site_url}/{segment(self.project)}/_build/results?buildId={segment(run_ref)}&view=logs"

    # ───────────────────────── queueing ─────────────────────────

    async def trigger(self, repo: str, ref: str, pipeline: str) -> str:
        return await self.queue(ref, pipeline)

    async def queue(
        self,
        ref: str,
        pipeline: str = "",
        *,
        commit: str | None = None,
        parameters: Mapping[str, Any] | None = None,
        variables: Mapping[str, str] | None = None,
    ) -> str:
        """Queue a run of `pipeline` (the connector's when empty) on a branch, at a commit when one is named.

        `ref` is a branch (`main`, or a full `refs/...`), or a 40-character commit, which is run as that
        commit on the repository's default branch. `parameters` are the pipeline's runtime parameters
        (`templateParameters`), `variables` plain, non-secret variables. Returns the run's id."""
        named = pipeline or self.pipeline
        if not named:
            raise ConfigurationError("ci/azure-pipelines: no `pipeline` to queue (a name or an id)")
        pipeline_id = await self._pipeline_id(named)
        source: dict[str, Any] = {}
        if SHA.match(ref):
            commit = commit or ref
        else:
            source["refName"] = ref if ref.startswith("refs/") else f"refs/heads/{ref}"
        if commit:
            source["version"] = commit
        body: dict[str, Any] = {"resources": {"repositories": {"self": source}}}
        if parameters:
            body["templateParameters"] = {str(k): str(v) for k, v in parameters.items()}
        if variables:
            body["variables"] = {str(k): {"value": str(v), "isSecret": False} for k, v in variables.items()}
        run = await self.client.request("POST", f"{self._scope}/pipelines/{pipeline_id}/runs", json=body)
        run_id = (run or {}).get("id")
        if run_id is None:
            raise UpstreamError("azure-pipelines", "the answer to a queued run carries no id")
        return str(run_id)

    # ───────────────────────── inbound ─────────────────────────

    def parse_event(self, headers: Mapping[str, str], body: bytes) -> list[InboundEvent]:
        """The `build.complete` and `ms.vss-pipelines.run-state-changed-event` service hooks."""
        payload: dict[str, Any] = json.loads(body or b"{}")
        events = parse_azure_devops_event("", payload)
        return [e for e in events if str(e.type).startswith("ci.")]

    async def test(self) -> dict[str, Any]:
        found = await self.client.request("GET", f"{self._scope}/pipelines", params={"$top": 1})
        return {"ok": isinstance(found, dict), "project": self.project}

    # ───────────────────────── lookups ─────────────────────────

    async def _repo_id(self) -> str:
        if self._repository_id is None:
            repo = await self.client.request(
                "GET", f"{self._scope}/git/repositories/{segment(self.repository)}"
            )
            self._repository_id = str((repo or {}).get("id") or "")
            if not self._repository_id:
                raise UpstreamError("azure-pipelines", f"the repository `{self.repository}` has no id")
        return self._repository_id

    async def _pipeline_id(self, named: str) -> str:
        """A pipeline's id: a number is one; a name is looked up once among the project's pipelines."""
        if named.isdigit():
            return named
        if self._pipelines is None:
            listed = await self.client.request("GET", f"{self._scope}/pipelines", params={"$top": 500})
            self._pipelines = {
                str(p.get("name", "")).casefold(): str(p["id"]) for p in (listed or {}).get("value", [])
            }
        found = self._pipelines.get(named.casefold())
        if found is None:
            raise ConfigurationError(f"ci/azure-pipelines: no pipeline named `{named}` in `{self.project}`")
        return found


def _timeline_status(record: Mapping[str, Any]) -> str:
    return {"pending": "notStarted", "inProgress": "inProgress", "completed": "completed"}.get(
        str(record.get("state")), "inProgress"
    )
