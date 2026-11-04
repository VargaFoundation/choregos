# SPDX-License-Identifier: Apache-2.0
"""The GitLab CiAdapter: pipeline status for a commit, job traces, pipeline trigger, pipeline events."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal
from urllib.parse import quote

from choregos_contracts import InboundEvent
from choregos_core.domain import CheckRun, CiStatus

from ..errors import ConfigurationError
from ..http import RestClient
from ..tracker.gitlab_events import parse_gitlab_event

State = Literal["pending", "running", "success", "failure", "error", "unknown"]
Progress = Literal["queued", "in_progress", "completed"]
Conclusion = Literal["success", "failure", "neutral", "cancelled", "timed_out", "skipped"]

_PIPELINE_STATE: dict[str, State] = {
    "success": "success",
    "failed": "failure",
    "running": "running",
    "pending": "pending",
    "created": "pending",
    "canceled": "error",
    "skipped": "error",
}

_JOB_RUN: dict[str, tuple[Progress, Conclusion | None]] = {
    "success": ("completed", "success"),
    "failed": ("completed", "failure"),
    "running": ("in_progress", None),
    "pending": ("queued", None),
    "created": ("queued", None),
    "waiting_for_resource": ("queued", None),
    "preparing": ("queued", None),
    "scheduled": ("queued", None),
    "canceled": ("completed", "cancelled"),
    "skipped": ("completed", "skipped"),
    "manual": ("completed", "neutral"),
}


class GitLabCi:
    def __init__(self, client: RestClient, project: str) -> None:
        if not project:
            raise ConfigurationError("ci/gitlab: `project` is required")
        self.client = client
        self.project = project

    @property
    def _base(self) -> str:
        return f"/api/v4/projects/{quote(self.project, safe='')}"

    async def status_for(self, repo: str, sha: str) -> CiStatus:
        pipelines = await self.client.request(
            "GET", f"{self._base}/pipelines", params={"sha": sha, "order_by": "id", "sort": "desc"}
        )
        if not isinstance(pipelines, list) or not pipelines:
            return CiStatus(state="unknown", sha=sha)
        latest = pipelines[0]
        state = _PIPELINE_STATE.get(str(latest.get("status", "")), "unknown")
        jobs = await self.client.request("GET", f"{self._base}/pipelines/{latest['id']}/jobs")
        runs: list[CheckRun] = []
        for job in jobs if isinstance(jobs, list) else []:
            status, conclusion = _JOB_RUN.get(str(job.get("status", "")), ("in_progress", None))
            runs.append(
                CheckRun(
                    name=str(job.get("name", "")),
                    status=status,
                    conclusion=conclusion,
                    url=job.get("web_url"),
                )
            )
        return CiStatus(state=state, runs=runs, url=latest.get("web_url"), sha=sha)

    async def logs(self, run_ref: str, tail: int = 500) -> str:
        """`run_ref` is a job id: GitLab serves the trace of a job, not of a pipeline."""
        payload = await self.client.request("GET", f"{self._base}/jobs/{run_ref}/trace", as_text=True)
        return "\n".join(str(payload or "").splitlines()[-tail:])

    async def trigger(self, repo: str, ref: str, pipeline: str) -> str:
        payload = await self.client.request("POST", f"{self._base}/pipeline", params={"ref": ref})
        return str((payload or {}).get("id", ""))

    def parse_event(self, headers: Mapping[str, str], body: bytes) -> list[InboundEvent]:
        payload: dict[str, Any] = json.loads(body or b"{}")
        delivery = headers.get("x-gitlab-event-uuid", headers.get("X-Gitlab-Event-UUID", ""))
        return parse_gitlab_event("Pipeline Hook", delivery, payload)

    async def test(self) -> dict[str, Any]:
        payload = await self.client.request("GET", self._base)
        return {"ok": bool(payload), "project": self.project}
