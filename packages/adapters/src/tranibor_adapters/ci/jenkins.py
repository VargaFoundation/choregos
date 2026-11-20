# SPDX-License-Identifier: Apache-2.0
"""The Jenkins CiAdapter: trigger a parameterised job, follow its queue item to the build, read the
result and the console, and normalise the notifications a Jenkins sends (S13-10).

Authentication is a user and an API token (HTTP basic). Jenkins protects its POSTs with a CSRF crumb:
it is asked for before a trigger and sent in the header Jenkins names; an instance with the crumb issuer
off answers 404 and nothing is sent. `job` is a path (`folder/app`); a multibranch job is addressed by
branch (`/job/app/job/feature%2Fx`).

A run is named by its queue item (`queue:<id>`, what `trigger` returns) or by the URL of its build.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote, urlsplit

from tranibor_contracts import InboundEvent, InboundEventType
from tranibor_core.domain import CheckRun, CiStatus

from ..errors import ConfigurationError, UpstreamError
from ..http import RestClient
from ..tracker.github_events import BRANCH_KEY_RE

State = Literal["pending", "running", "success", "failure", "error", "unknown"]
Progress = Literal["queued", "in_progress", "completed"]
Conclusion = Literal["success", "failure", "neutral", "cancelled", "timed_out", "skipped"]

#: Jenkins' `result` of a finished build. UNSTABLE (tests failed, build finished) blocks like a failure.
_RESULT: dict[str, tuple[State, Conclusion]] = {
    "SUCCESS": ("success", "success"),
    "UNSTABLE": ("failure", "failure"),
    "FAILURE": ("failure", "failure"),
    "ABORTED": ("error", "cancelled"),
    "NOT_BUILT": ("error", "skipped"),
}

#: The fields read of a build, and the two places a commit is named: the revision the SCM built and a
#: parameter the pipeline was given.
_BUILD_TREE = "number,url,result,building,actions[lastBuiltRevision[SHA1],parameters[name,value]]"
_QUEUE_ITEM = re.compile(r"/queue/item/(?P<id>\d+)")
QUEUE_PREFIX = "queue:"

Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class JenkinsBuild:
    """Where a queue item stands: still waiting, cancelled, or started as build `number` at `path`."""

    queued: bool = False
    cancelled: bool = False
    number: int | None = None
    path: str | None = None


def job_path(job: str, branch: str | None = None) -> str:
    """`folder/app` -> `/job/folder/job/app`; a multibranch branch is one more segment, `/` encoded."""
    segments = [part for part in job.strip("/").split("/") if part]
    if branch:
        segments.append(branch.removeprefix("refs/heads/"))
    return "".join(f"/job/{quote(part, safe='')}" for part in segments)


def _state_of(build: Mapping[str, Any]) -> tuple[State, Progress, Conclusion | None]:
    result = build.get("result")
    if result:
        state, conclusion = _RESULT.get(str(result), ("error", "neutral"))
        return state, "completed", conclusion
    if build.get("building"):
        return "running", "in_progress", None
    return "pending", "queued", None


def _names_sha(build: Mapping[str, Any], sha: str) -> bool:
    for action in build.get("actions") or []:
        if not isinstance(action, Mapping):
            continue
        if (action.get("lastBuiltRevision") or {}).get("SHA1") == sha:
            return True
        for parameter in action.get("parameters") or []:
            if isinstance(parameter, Mapping) and parameter.get("value") == sha:
                return True
    return False


class JenkinsCi:
    def __init__(  # noqa: PLR0913 - one connector's settings, all keyword-only
        self,
        client: RestClient,
        job: str,
        *,
        multibranch: bool = False,
        parameters: Mapping[str, str] | None = None,
        ref_parameter: str = "BRANCH",
        crumb: bool = True,
        poll_attempts: int = 10,
        poll_interval: float = 3.0,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if not job.strip("/"):
            raise ConfigurationError("ci/jenkins: `job` is required")
        self.client = client
        self.job = job
        self.multibranch = multibranch
        self.parameters = dict(parameters or {})
        self.ref_parameter = ref_parameter
        self.crumb = crumb
        self.poll_attempts = poll_attempts
        self.poll_interval = poll_interval
        self._sleep = sleep

    # --- reading -------------------------------------------------------------------------------

    async def status_for(self, repo: str, sha: str) -> CiStatus:
        """The newest build that built `sha` (its SCM revision, or a parameter holding it). Jenkins
        has no lookup by commit: the last builds are read and filtered, so a build older than the window
        is not seen (`unknown`)."""
        found = [(name, build) for name, build in await self._recent_builds() if _names_sha(build, sha)]
        if not found:
            return CiStatus(state="unknown", sha=sha)
        name, build = max(found, key=lambda item: int(item[1].get("number") or 0))
        state, progress, conclusion = _state_of(build)
        console = f"{str(build.get('url') or '').rstrip('/')}/console" if build.get("url") else None
        run = CheckRun(name=name, status=progress, conclusion=conclusion, url=console)
        return CiStatus(state=state, runs=[run], url=console, sha=sha)

    async def _recent_builds(self) -> list[tuple[str, dict[str, Any]]]:
        base = job_path(self.job)
        if not self.multibranch:
            payload = await self.client.request(
                "GET", f"{base}/api/json", params={"tree": f"builds[{_BUILD_TREE}]{{0,50}}"}
            )
            return [(self.job, b) for b in (payload or {}).get("builds", [])]
        payload = await self.client.request(
            "GET", f"{base}/api/json", params={"tree": f"jobs[name,builds[{_BUILD_TREE}]{{0,20}}]"}
        )
        return [
            (f"{self.job}/{branch.get('name', '')}", b)
            for branch in (payload or {}).get("jobs", [])
            for b in branch.get("builds", [])
        ]

    async def build_of(self, run_ref: str) -> JenkinsBuild:
        """Resolve a run reference to its build. A queue item the controller has forgotten (404, they are
        kept a few minutes after they start) is read as cancelled: its build cannot be found any more."""
        if not run_ref.startswith(QUEUE_PREFIX):
            return JenkinsBuild(path=self._path_of(run_ref))
        try:
            item = await self.client.request("GET", f"/queue/item/{run_ref[len(QUEUE_PREFIX) :]}/api/json")
        except UpstreamError as error:
            if error.status_code == 404:
                return JenkinsBuild(cancelled=True)
            raise
        if item.get("cancelled"):
            return JenkinsBuild(cancelled=True)
        executable = item.get("executable")
        if not executable:
            return JenkinsBuild(queued=True)
        return JenkinsBuild(number=executable.get("number"), path=self._path_of(str(executable["url"])))

    async def wait_for_build(self, run_ref: str) -> JenkinsBuild:
        """Poll the queue item until it became a build (or was cancelled), `poll_attempts` times."""
        build = await self.build_of(run_ref)
        for _ in range(self.poll_attempts - 1):
            if not build.queued:
                break
            await self._sleep(self.poll_interval)
            build = await self.build_of(run_ref)
        return build

    async def build_status(self, run_ref: str) -> CiStatus:
        """The state, result and console URL of the build a run reference names."""
        build = await self.build_of(run_ref)
        if build.cancelled:
            return CiStatus(state="error", runs=[CheckRun(name=self.job, conclusion="cancelled")])
        if build.path is None:
            return CiStatus(state="pending", runs=[CheckRun(name=self.job, status="queued", conclusion=None)])
        data = await self.client.request("GET", f"{build.path}/api/json", params={"tree": _BUILD_TREE})
        state, progress, conclusion = _state_of(data)
        console = f"{str(data.get('url') or '').rstrip('/')}/console" if data.get("url") else None
        return CiStatus(
            state=state,
            runs=[CheckRun(name=self.job, status=progress, conclusion=conclusion, url=console)],
            url=console,
        )

    async def logs(self, run_ref: str, tail: int = 500) -> str:
        """The tail of the build's console. A build still in the queue has none: empty."""
        build = await self.build_of(run_ref)
        if build.path is None:
            return ""
        text = await self.client.request("GET", f"{build.path}/consoleText", as_text=True)
        return "\n".join(str(text or "").splitlines()[-tail:])

    def _path_of(self, build_url: str) -> str:
        """The path of a build URL under this client's base (Jenkins may sit behind a context path)."""
        path = urlsplit(build_url).path or build_url
        prefix = urlsplit(self.client.base_url).path.rstrip("/")
        if prefix and path.startswith(prefix + "/"):
            path = path[len(prefix) :]
        return path.rstrip("/")

    # --- triggering ----------------------------------------------------------------------------

    async def trigger(self, repo: str, ref: str, pipeline: str) -> str:
        """Queue the job (`pipeline` overrides it) and return `queue:<id>`. A multibranch job is built on
        the branch `ref`; a plain one receives `ref` as the parameter `ref_parameter`."""
        job = pipeline or self.job
        params = dict(self.parameters)
        if self.multibranch:
            path = job_path(job, ref)
        else:
            path = job_path(job)
            if self.ref_parameter:
                params[self.ref_parameter] = ref
        endpoint = "buildWithParameters" if params else "build"
        headers = await self._crumb_header()
        _, response_headers = await self.client.request(
            "POST", f"{path}/{endpoint}", params=params or None, headers=headers, with_headers=True
        )
        match = _QUEUE_ITEM.search(response_headers.get("location", ""))
        if match is None:
            raise UpstreamError("jenkins", f"no queue item in the answer to {path}/{endpoint}")
        return f"{QUEUE_PREFIX}{match.group('id')}"

    async def _crumb_header(self) -> dict[str, str]:
        if not self.crumb:
            return {}
        try:
            payload = await self.client.request("GET", "/crumbIssuer/api/json")
        except UpstreamError as error:
            if error.status_code == 404:  # the crumb issuer is off: nothing to send
                return {}
            raise
        field, value = (payload or {}).get("crumbRequestField"), (payload or {}).get("crumb")
        return {str(field): str(value)} if field and value else {}

    # --- inbound -------------------------------------------------------------------------------

    def parse_event(self, headers: Mapping[str, str], body: bytes) -> list[InboundEvent]:
        return parse_jenkins_event(json.loads(body or b"{}"))

    async def test(self) -> dict[str, Any]:
        payload = await self.client.request(
            "GET", f"{job_path(self.job)}/api/json", params={"tree": "name,buildable"}
        )
        return {"ok": bool(payload), "job": self.job}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _repo_of(url: str) -> str | None:
    """`owner/repo` of a git URL (https or scp-like); None when it does not look like one."""
    match = re.search(r"[:/](?P<repo>[^/:]+/[^/]+?)(?:\.git)?/?$", url or "")
    return match.group("repo") if match else None


def parse_jenkins_event(payload: dict[str, Any]) -> list[InboundEvent]:
    """A Jenkins notification, as an `InboundEvent`.

    Two shapes are read: the **Notification plugin**'s (`{"name", "build": {"number", "phase", "status",
    "parameters", "scm": {"url", "branch", "commit"}}}`: `STARTED` starts, `COMPLETED` ends, the other
    phases say nothing new) and the **flat** one a pipeline posts (Generic Webhook Trigger's sibling
    `httpRequest` step): `{"job", "build_number", "result", "sha", "branch", "project", "work_item"}`.
    The work item is the one the payload names (`work_item`, or the parameter `CHOREGOS_WORK_ITEM`), else
    the ticket of a `choregos/<n>-...` branch, as for GitHub."""
    build = payload.get("build")
    flat: dict[str, Any]
    if isinstance(build, dict):
        phase = str(build.get("phase", "")).upper()
        job = str(payload.get("name", ""))
        number = build.get("number")
        params: dict[str, Any] = _dict(build.get("parameters"))
        scm: dict[str, Any] = _dict(build.get("scm"))
        result = str(build.get("status", "")).upper()
        flat = {
            "job": job,
            "build_number": number,
            "sha": scm.get("commit"),
            "branch": scm.get("branch"),
            "repo": _repo_of(str(scm.get("url", ""))),
            "url": build.get("full_url") or build.get("url"),
            "project": params.get("CHOREGOS_PROJECT"),
            "work_item": params.get("CHOREGOS_WORK_ITEM"),
        }
        if phase == "STARTED":
            kind = InboundEventType.CI_STARTED
        elif phase == "COMPLETED":
            kind = InboundEventType.CI_SUCCEEDED if result == "SUCCESS" else InboundEventType.CI_FAILED
        else:
            return []
    else:
        phase = str(payload.get("phase", "")).upper()
        result = str(payload.get("result") or payload.get("status") or "").upper()
        flat = {
            "job": str(payload.get("job", "")),
            "build_number": payload.get("build_number"),
            "sha": payload.get("sha"),
            "branch": payload.get("branch"),
            "repo": payload.get("repo"),
            "url": payload.get("url"),
            "project": payload.get("project"),
            "work_item": payload.get("work_item"),
        }
        if phase == "STARTED" or not result:
            kind = InboundEventType.CI_STARTED
        else:
            kind = InboundEventType.CI_SUCCEEDED if result == "SUCCESS" else InboundEventType.CI_FAILED
    repo = flat["repo"]
    key = flat["work_item"]
    if not key and repo:
        branch = BRANCH_KEY_RE.search(str(flat["branch"] or ""))
        key = f"{repo}#{branch.group('number')}" if branch else None
    slug = flat["project"] or (str(repo).split("/")[-1] if repo else None)
    return [
        InboundEvent(
            type=kind,
            source="jenkins",
            delivery_id=_delivery_id(flat, kind),
            project_slug=slug,
            work_item_key=key,
            payload={
                "job": flat["job"],
                "build_number": flat["build_number"],
                "result": result or None,
                "sha": flat["sha"],
                "branch": flat["branch"],
                "url": flat["url"],
            },
        )
    ]


def _delivery_id(flat: dict[str, Any], kind: InboundEventType) -> str:
    """The id a redelivery of this build event shares, and no other controller's build does.

    A job name and a build number repeat across controllers (two organisations each run `app` #7), and
    the dedup table is global: the build's absolute URL names the controller as well. A payload with only
    a relative or no URL falls back to job and number, which cannot tell controllers apart."""
    url = str(flat["url"] or "")
    if url.startswith(("http://", "https://")):
        return f"jenkins:{url.rstrip('/')}:{kind.value}"
    return f"jenkins:{flat['job']}:{flat['build_number']}:{kind.value}"
