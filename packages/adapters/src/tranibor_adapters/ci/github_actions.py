# SPDX-License-Identifier: Apache-2.0
"""The GitHub Actions CiAdapter: dispatch a workflow on a ref, find the run it started, read run and job
conclusions, rerun the failed jobs, and read the `workflow_run` webhooks the GitHub App receives (S13-10).

It speaks through the same `GitHubClient` as the SCM (App installation token, rate limits). The App needs
the `actions` permission (write: dispatch and rerun) and the `workflow_run` event: the platform asks for
both (`APP_PERMISSIONS`, `APP_EVENTS`). Without the permission the token is asked again without it (it is
optional, like `statuses`) and these calls are refused by GitHub with 403.

`workflow_dispatch` answers 204 and names no run: the run is found afterwards, among the runs of that
workflow on that ref that did not exist before the dispatch.

A run is named `<run id>`, or `owner/repo@<run id>` for another repository than the configured one.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal

from tranibor_contracts import InboundEvent
from tranibor_core.domain import CheckRun, CiStatus

from ..errors import ConfigurationError, UpstreamError
from ..github.client import GitHubClient
from ..tracker.github_events import parse_github_event

State = Literal["pending", "running", "success", "failure", "error", "unknown"]
Progress = Literal["queued", "in_progress", "completed"]
Conclusion = Literal["success", "failure", "neutral", "cancelled", "timed_out", "skipped"]

#: GitHub's `conclusion` of a finished run or job, as the platform's check conclusion.
_CONCLUSION: dict[str, Conclusion] = {
    "success": "success",
    "failure": "failure",
    "startup_failure": "failure",
    "neutral": "neutral",
    "skipped": "skipped",
    "cancelled": "cancelled",
    "timed_out": "timed_out",
    "stale": "cancelled",
    "action_required": "neutral",
}
#: What each conclusion makes of the commit: `neutral` and `skipped` do not block (as GitHub counts
#: them), a cancelled or stale run is neither a pass nor a failure of the code (`error`), and a run
#: waiting for someone's approval (`action_required`) is pending.
_STATE: dict[str, State] = {
    "success": "success",
    "neutral": "success",
    "skipped": "success",
    "failure": "failure",
    "startup_failure": "failure",
    "timed_out": "failure",
    "cancelled": "error",
    "stale": "error",
    "action_required": "pending",
}
_PROGRESS: dict[str, Progress] = {"queued": "queued", "waiting": "queued", "pending": "queued"}

Sleep = Callable[[float], Awaitable[None]]


def _progress(status: str) -> Progress:
    if status == "completed":
        return "completed"
    return _PROGRESS.get(status, "in_progress")


def _run_state(run: Mapping[str, Any]) -> State:
    if run.get("status") == "completed":
        return _STATE.get(str(run.get("conclusion")), "error")
    return "pending" if _progress(str(run.get("status"))) == "queued" else "running"


class GitHubActionsCi:
    def __init__(
        self,
        client: GitHubClient,
        repo: str,
        *,
        workflow: str = "",
        inputs: Mapping[str, str] | None = None,
        poll_attempts: int = 10,
        poll_interval: float = 3.0,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if "/" not in repo:
            raise ConfigurationError("ci/github-actions: `repo` is required, as owner/repo")
        self.client = client
        self.repo = repo
        self.workflow = workflow
        self.inputs = {k: str(v) for k, v in (inputs or {}).items()}
        self.poll_attempts = poll_attempts
        self.poll_interval = poll_interval
        self._sleep = sleep

    @staticmethod
    def _split(run_ref: str, default_repo: str) -> tuple[str, str]:
        repo, sep, run_id = run_ref.rpartition("@")
        return (repo, run_id) if sep and repo else (default_repo, run_ref)

    # --- reading -------------------------------------------------------------------------------

    async def status_for(self, repo: str, sha: str) -> CiStatus:
        """Every workflow that ran on `sha`, newest run of each (a rerun replaces its run): failing if
        one fails, running while one is not finished, success when all passed. The jobs are the checks.
        With a configured `workflow`, only that one is read."""
        repo = repo or self.repo
        payload = await self.client.request(
            "GET", f"/repos/{repo}/actions/runs", repo=repo, params={"head_sha": sha, "per_page": 100}
        )
        latest: dict[Any, dict[str, Any]] = {}
        for run in (payload or {}).get("workflow_runs", []):
            if self.workflow and not self._is_configured_workflow(run):
                continue
            key = run.get("workflow_id", run.get("name"))
            if key not in latest or str(run.get("created_at", "")) > str(latest[key].get("created_at", "")):
                latest[key] = run
        if not latest:
            return CiStatus(state="unknown", sha=sha)
        runs: list[CheckRun] = []
        for run in latest.values():
            jobs = await self.client.request(
                "GET", f"/repos/{repo}/actions/runs/{run['id']}/jobs", repo=repo, params={"per_page": 100}
            )
            for job in (jobs or {}).get("jobs", []):
                runs.append(
                    CheckRun(
                        name=f"{run.get('name', '')} / {job.get('name', '')}",
                        status=_progress(str(job.get("status"))),
                        conclusion=_CONCLUSION.get(str(job.get("conclusion"))),
                        url=job.get("html_url"),
                    )
                )
        states = [_run_state(run) for run in latest.values()]
        for outcome in ("failure", "running", "pending", "error"):
            if outcome in states:
                state: State = outcome
                break
        else:
            state = "success"
        newest = max(latest.values(), key=lambda run: str(run.get("created_at", "")))
        return CiStatus(state=state, runs=runs, url=newest.get("html_url"), sha=sha)

    def _is_configured_workflow(self, run: Mapping[str, Any]) -> bool:
        path = str(run.get("path", "")).rsplit("/", 1)[-1].split("@", 1)[0]
        return self.workflow in {path, str(run.get("workflow_id"))}

    async def run_status(self, run_ref: str) -> CiStatus:
        """One run, by id: its status, conclusion and jobs."""
        repo, run_id = self._split(run_ref, self.repo)
        run = await self.client.request("GET", f"/repos/{repo}/actions/runs/{run_id}", repo=repo)
        jobs = await self.client.request(
            "GET", f"/repos/{repo}/actions/runs/{run_id}/jobs", repo=repo, params={"per_page": 100}
        )
        checks = [
            CheckRun(
                name=str(job.get("name", "")),
                status=_progress(str(job.get("status"))),
                conclusion=_CONCLUSION.get(str(job.get("conclusion"))),
                url=job.get("html_url"),
            )
            for job in (jobs or {}).get("jobs", [])
        ]
        return CiStatus(state=_run_state(run), runs=checks, url=run.get("html_url"), sha=run.get("head_sha"))

    async def logs(self, run_ref: str, tail: int = 500) -> str:
        """The tail of the log of each job of the run, under its name."""
        repo, run_id = self._split(run_ref, self.repo)
        jobs = await self.client.request(
            "GET", f"/repos/{repo}/actions/runs/{run_id}/jobs", repo=repo, params={"per_page": 100}
        )
        chunks: list[str] = []
        for job in (jobs or {}).get("jobs", []):
            text = await self.client.request_text(f"/repos/{repo}/actions/jobs/{job['id']}/logs", repo=repo)
            chunks.append(f"== {job.get('name', '')}\n" + "\n".join(text.splitlines()[-tail:]))
        return "\n".join(chunks)

    # --- acting --------------------------------------------------------------------------------

    async def trigger(self, repo: str, ref: str, pipeline: str) -> str:
        """Dispatch the workflow (`pipeline` overrides it) on `ref` with the configured inputs, then find
        the run it started and return its id."""
        repo = repo or self.repo
        workflow = pipeline or self.workflow
        if not workflow:
            raise ConfigurationError("ci/github-actions: `workflow` is required to trigger a run")
        runs_path = f"/repos/{repo}/actions/workflows/{workflow}/runs"
        params = {"event": "workflow_dispatch", "branch": ref.removeprefix("refs/heads/"), "per_page": 30}
        before = {run["id"] for run in await self._runs(runs_path, repo, params)}
        body: dict[str, Any] = {"ref": ref}
        if self.inputs:
            body["inputs"] = self.inputs
        await self.client.request(
            "POST", f"/repos/{repo}/actions/workflows/{workflow}/dispatches", repo=repo, json=body
        )
        for attempt in range(self.poll_attempts):
            if attempt:
                await self._sleep(self.poll_interval)
            new = [run for run in await self._runs(runs_path, repo, params) if run["id"] not in before]
            if new:
                return str(min(run["id"] for run in new))
        raise UpstreamError(
            "github", f"the dispatch of {workflow} on {ref} started no run that could be found"
        )

    async def _runs(self, path: str, repo: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        payload = await self.client.request("GET", path, repo=repo, params=params)
        runs: list[dict[str, Any]] = (payload or {}).get("workflow_runs", [])
        return runs

    async def rerun_failed(self, run_ref: str) -> None:
        """Rerun the failed jobs of a finished run (and the jobs that depend on them)."""
        repo, run_id = self._split(run_ref, self.repo)
        await self.client.request("POST", f"/repos/{repo}/actions/runs/{run_id}/rerun-failed-jobs", repo=repo)

    async def cancel(self, run_ref: str) -> None:
        repo, run_id = self._split(run_ref, self.repo)
        await self.client.request("POST", f"/repos/{repo}/actions/runs/{run_id}/cancel", repo=repo)

    # --- inbound -------------------------------------------------------------------------------

    def parse_event(self, headers: Mapping[str, str], body: bytes) -> list[InboundEvent]:
        lowered = {k.lower(): v for k, v in headers.items()}
        return parse_github_event(
            lowered.get("x-github-event", "workflow_run"),
            lowered.get("x-github-delivery", ""),
            json.loads(body or b"{}"),
        )

    async def test(self) -> dict[str, Any]:
        path = f"/repos/{self.repo}/actions/workflows" + (f"/{self.workflow}" if self.workflow else "")
        payload = await self.client.request("GET", path, repo=self.repo)
        return {"ok": bool(payload), "repo": self.repo, "workflow": self.workflow}
