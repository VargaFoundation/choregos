# SPDX-License-Identifier: Apache-2.0
"""A scriptable Azure DevOps server for Repos and Pipelines, as an `httpx` transport: no network (S13-08).

It answers the calls `AzureReposScm` and `AzurePipelinesCi` make, from an in-memory organisation: Git
branches and commits (each with its whole tree), pushes with their `oldObjectId` check, pull requests with
iterations, statuses, votes, labels and policy evaluations, a completion that enforces `lastMergeSourceCommit`
and the Build/Status policies, builds with timelines and logs, the Pipelines runs API, and the identities API.
It checks what a real server checks and a loose mock does not: the `api-version`, the credential (a PAT as
Basic with an empty user name, or the bearer token its own Entra endpoint issued) and the stale
`oldObjectId` of a push.

It proves the adapters speak the protocol Microsoft documents (written from the REST API 7.1 reference);
it does not prove a real organisation accepts exactly this: **no Azure DevOps organisation was reachable**.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Any
from urllib.parse import unquote

import httpx

BUILD_POLICY = "0609b952-1397-4640-95ec-e00a01b2c241"
STATUS_POLICY = "cbdc66da-9728-4af8-aada-9a5a32e4a226"
REVIEWERS_POLICY = "fa4e907d-c16b-4a4c-9dfa-4906e5d171dd"
ZERO = "0" * 40


class FakeAzureDevOpsServer:
    """`transport()` backs a `RestClient`'s `httpx.AsyncClient`; `calls` records each request."""

    def __init__(
        self,
        project: str = "Billing",
        repository: str = "billing-api",
        *,
        organisation: str = "northwind",
        pat: str = "pat",
        entra_secret: str | None = None,
        completion_polls_needed: int = 0,
    ) -> None:
        self.project = project
        self.project_id = "11111111-1111-1111-1111-111111111111"
        self.repository = repository
        self.repository_id = "22222222-2222-2222-2222-222222222222"
        self.organisation = organisation
        self.pat = pat
        self.entra_secret = entra_secret
        self.issued_token = "entra-access-token"
        #: How many reads of a completing pull request still say `active` after the completion call.
        self.completion_polls_needed = completion_polls_needed
        self.calls: list[tuple[str, str]] = []
        self.bodies: list[Any] = []
        self.commits: dict[str, dict[str, Any]] = {}
        self.refs: dict[str, str] = {}
        self.prs: dict[int, dict[str, Any]] = {}
        self.threads: dict[int, list[dict[str, Any]]] = {}
        self.statuses: dict[int, list[dict[str, Any]]] = {}
        self.labels: dict[int, list[str]] = {}
        self.iterations: dict[int, list[dict[str, Any]]] = {}
        self.evaluations: dict[int, list[dict[str, Any]]] = {}
        self.commit_statuses: list[dict[str, Any]] = []
        self.builds: list[dict[str, Any]] = []
        self.timelines: dict[int, list[dict[str, Any]]] = {}
        self.logs: dict[int, list[str]] = {}
        self.pipelines = {7: "ci", 8: "release"}
        self.runs: list[dict[str, Any]] = []
        self.identities = [
            {
                "id": "33333333-3333-3333-3333-333333333333",
                "providerDisplayName": "Ada Lovelace",
                "properties": {"Account": {"$value": "ada@northwind.test"}},
            }
        ]
        #: The options of the last completion asked, as the server received them.
        self.completion: dict[str, Any] = {}
        self._counter = 0
        self._polled: dict[int, int] = {}
        self.push("main", {"README.md": "# billing\n"})

    # ───────────────────────── scripting ─────────────────────────

    def push(self, branch: str, files: dict[str, str | None]) -> str:
        """A commit on `branch` (created from `main` if new): `None` deletes a file. Returns its id."""
        name = f"refs/heads/{branch}"
        parent = self.refs.get(name) or self.refs.get("refs/heads/main")
        tree = dict(self.commits[parent]["tree"]) if parent else {}
        for path, content in files.items():
            if content is None:
                tree.pop(path, None)
            else:
                tree[path] = content
        self._counter += 1
        sha = hashlib.sha1(f"{branch}:{self._counter}".encode(), usedforsecurity=False).hexdigest()
        self.commits[sha] = {"tree": tree, "parent": parent}
        self.refs[name] = sha
        for pr in self.prs.values():
            if pr["sourceRefName"] == name and pr["status"] == "active":
                self._iterate(pr, sha)
        return sha

    def _iterate(self, pr: dict[str, Any], sha: str) -> None:
        pr["lastMergeSourceCommit"] = {"commitId": sha}
        found = self.iterations[pr["pullRequestId"]]
        found.append({"id": len(found) + 1, "sourceRefCommit": {"commitId": sha}})

    def vote(self, number: int, reviewer: str, vote: int, *, container: bool = False) -> None:
        self.prs[number]["reviewers"] = [
            r for r in self.prs[number]["reviewers"] if r["uniqueName"] != reviewer
        ] + [{"uniqueName": reviewer, "displayName": reviewer, "vote": vote, "isContainer": container}]

    def add_status(
        self,
        number: int,
        name: str,
        state: str,
        *,
        genre: str = "ci",
        iteration: int | None = None,
        when: str = "",
    ) -> None:
        stamp = when or f"2026-11-20T10:00:{len(self.statuses.get(number, [])):02d}Z"
        self.statuses.setdefault(number, []).append(
            {
                "state": state,
                "context": {"name": name, "genre": genre},
                "targetUrl": f"https://ci.example/{name}",
                "description": f"{name} {state}",
                "creationDate": stamp,
                **({"iterationId": iteration} if iteration is not None else {}),
            }
        )

    def add_policy(self, number: int, kind: str, name: str, status: str) -> None:
        self.evaluations.setdefault(number, []).append(
            {
                "status": status,
                "configuration": {
                    "type": {"id": kind, "displayName": name},
                    "settings": {"displayName": name},
                },
            }
        )

    def add_build(
        self,
        pipeline: int,
        sha: str,
        *,
        branch: str = "main",
        status: str = "completed",
        result: str | None = "succeeded",
        pull_request: int | None = None,
        jobs: list[tuple[str, str, str | None]] | None = None,
        log: list[str] | None = None,
    ) -> int:
        """A build of `pipeline` at `sha`. For a pull request's build, `sourceVersion` is a merge commit and
        `triggerInfo` names the pull request's source commit, as Azure does."""
        build_id = len(self.builds) + 100
        build: dict[str, Any] = {
            "id": build_id,
            "buildNumber": f"2026.{build_id}",
            "status": status,
            "definition": {"id": pipeline, "name": self.pipelines[pipeline]},
            "repository": {"id": self.repository_id},
            "sourceBranch": f"refs/pull/{pull_request}/merge" if pull_request else f"refs/heads/{branch}",
            "sourceVersion": "e" * 40 if pull_request else sha,
            "_links": {
                "web": {
                    "href": f"https://dev.azure.com/{self.organisation}/{self.project}/_build/results?buildId={build_id}"
                }
            },
        }
        if result:
            build["result"] = result
        if pull_request:
            build["triggerInfo"] = {"pr.number": str(pull_request), "pr.sourceCommitId": sha}
        self.builds.append(build)
        self.timelines[build_id] = [
            {"type": "Job", "name": name, "state": state, **({"result": res} if res else {})}
            for name, state, res in (jobs or [])
        ] + [{"type": "Task", "name": "checkout", "state": "completed", "result": "succeeded"}]
        self.logs[build_id] = log or []
        return build_id

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    # ───────────────────────── the server ─────────────────────────

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = unquote(request.url.path)
        self.calls.append((request.method, path))
        if request.method in {"POST", "PUT", "PATCH"} and request.content:
            self.bodies.append(
                json.loads(request.content) if "json" in request.headers.get("content-type", "") else None
            )
        if "oauth2/v2.0/token" in path:
            return self._token(request)
        denied = self._authorise(request)
        if denied is not None:
            return denied
        if "api-version" not in request.url.params:
            return _error(400, "VS402337: the api-version is required")
        route = _strip(path, self.organisation)
        if route.endswith("/_apis/identities"):
            wanted = request.url.params.get("filterValue", "").casefold()
            found = [i for i in self.identities if wanted in json.dumps(i).casefold()]
            return httpx.Response(200, json={"value": found})
        prefix = f"/{self.project}/_apis/"
        if not route.startswith(prefix):
            return _error(404, f"no such project in {route}")
        rest = route[len(prefix) :]
        for pattern, handler in self._routes():
            match = re.fullmatch(pattern, rest)
            if match:
                response: httpx.Response = handler(request, *match.groups())
                return response
        return _error(404, f"unscripted route {request.method} {rest}")

    def _routes(self) -> list[tuple[str, Any]]:
        git = rf"git/repositories/(?:{re.escape(self.repository)}|{self.repository_id})"
        return [
            (git, self._repo),
            (rf"{git}/refs", self._refs),
            (rf"{git}/pushes", self._pushes),
            (rf"{git}/items", self._items),
            (rf"{git}/diffs/commits", self._diffs),
            (rf"{git}/commits/([0-9a-f]{{40}})/statuses", self._commit_status),
            (rf"{git}/pullrequests", self._pulls),
            (rf"{git}/pullrequests/(\d+)", self._pull),
            (rf"{git}/pullrequests/(\d+)/threads", self._thread),
            (rf"{git}/pullrequests/(\d+)/iterations", self._iterations),
            (rf"{git}/pullrequests/(\d+)/iterations/(\d+)/changes", self._changes),
            (rf"{git}/pullrequests/(\d+)/statuses", self._pr_statuses),
            (rf"{git}/pullrequests/(\d+)/labels", self._pr_labels),
            (rf"{git}/pullrequests/(\d+)/reviewers/([0-9a-f-]{{36}})", self._reviewer),
            (r"policy/evaluations", self._policies),
            (r"build/builds", self._builds),
            (r"build/builds/(\d+)", self._build),
            (r"build/builds/(\d+)/timeline", self._timeline),
            (r"build/builds/(\d+)/logs", self._log_list),
            (r"build/builds/(\d+)/logs/(\d+)", self._log),
            (r"pipelines", self._pipeline_list),
            (r"pipelines/(\d+)/runs", self._run),
        ]

    # ───── auth ─────

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
        if self.entra_secret is None or unquote(form.get("client_secret", "")) != self.entra_secret:
            return httpx.Response(401, json={"error": "invalid_client"})
        return httpx.Response(200, json={"access_token": self.issued_token, "expires_in": 3600})

    def _authorise(self, request: httpx.Request) -> httpx.Response | None:
        header = request.headers.get("authorization", "")
        scheme, _, value = header.partition(" ")
        if scheme == "Bearer" and value == self.issued_token:
            return None
        if scheme == "Basic":
            user, _, password = base64.b64decode(value).decode().partition(":")
            if user == "" and password == self.pat:
                return None
        return httpx.Response(203, text="<html>Sign in</html>")

    # ───── git ─────

    def _repo(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": self.repository_id,
                "name": self.repository,
                "defaultBranch": "refs/heads/main",
                "project": {"id": self.project_id, "name": self.project},
            },
        )

    def _refs(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            wanted = "refs/" + request.url.params.get("filter", "")
            return httpx.Response(
                200,
                json={
                    "value": [
                        {"name": n, "objectId": s} for n, s in self.refs.items() if n.startswith(wanted)
                    ]
                },
            )
        results = []
        for update in json.loads(request.content):
            ok = update["oldObjectId"] == ZERO and update["name"] not in self.refs
            if ok:
                self.refs[update["name"]] = update["newObjectId"]
            results.append(
                {
                    "name": update["name"],
                    "success": ok,
                    "updateStatus": "succeeded" if ok else "staleOldObjectId",
                }
            )
        return httpx.Response(200, json={"value": results})

    def _pushes(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        update = body["refUpdates"][0]
        branch = update["name"].removeprefix("refs/heads/")
        if self.refs.get(update["name"]) != update["oldObjectId"]:
            return _error(400, "TF401028: the reference has been updated (staleOldObjectId)")
        files: dict[str, str | None] = {}
        for change in body["commits"][0]["changes"]:
            path = change["item"]["path"].lstrip("/")
            exists = path in self.commits[self.refs[update["name"]]]["tree"]
            if (change["changeType"] == "add") == exists:
                return _error(400, f"TF401029: {change['changeType']} of {path} contradicts the tree")
            files[path] = change["newContent"]["content"]
        sha = self.push(branch, files)
        return httpx.Response(201, json={"commits": [{"commitId": sha}], "pushId": self._counter})

    def _resolve(self, version: str, kind: str) -> str | None:
        return (
            self.refs.get(f"refs/heads/{version}")
            if kind == "branch"
            else (version if version in self.commits else None)
        )

    def _items(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        sha = self._resolve(
            params.get("versionDescriptor.version", ""), params.get("versionDescriptor.versionType", "branch")
        )
        if sha is None:
            return _error(404, "TF401175: the version does not exist")
        tree = self.commits[sha]["tree"]
        if "scopePath" in params:
            folder = params["scopePath"].strip("/")
            names = sorted(p for p in tree if p.startswith(folder + "/" if folder else ""))
            if not names:
                return _error(404, "TF401174: the folder does not exist")
            return httpx.Response(
                200,
                json={
                    "value": [{"path": f"/{p}", "isFolder": False, "gitObjectType": "blob"} for p in names]
                },
            )
        path = params.get("path", "").lstrip("/")
        if path not in tree:
            return _error(404, f"TF401174: the item {path} could not be found")
        return httpx.Response(200, json={"path": f"/{path}", "content": tree[path], "isFolder": False})

    def _diffs(self, request: httpx.Request) -> httpx.Response:
        p = request.url.params
        base = self._resolve(p["baseVersion"], p["baseVersionType"])
        target = self._resolve(p["targetVersion"], p["targetVersionType"])
        if base is None or target is None:
            return _error(404, "TF401175: the version does not exist")
        return httpx.Response(
            200, json={"changes": self._changes_between(base, target), "allChangesIncluded": True}
        )

    def _changes_between(self, base: str, target: str) -> list[dict[str, Any]]:
        before, after = self.commits[base]["tree"], self.commits[target]["tree"]
        out = []
        for path in sorted(set(before) | set(after)):
            if before.get(path) == after.get(path):
                continue
            kind = "add" if path not in before else "delete" if path not in after else "edit"
            out.append({"changeType": kind, "item": {"path": f"/{path}"}})
        return out

    def _commit_status(self, request: httpx.Request, sha: str) -> httpx.Response:
        body = json.loads(request.content)
        self.commit_statuses.append({"sha": sha, **body})
        return httpx.Response(201, json=body)

    # ───── pull requests ─────

    def _view(self, pr: dict[str, Any]) -> dict[str, Any]:
        view = dict(pr)
        number = pr["pullRequestId"]
        if pr.get("_completing"):
            seen = self._polled.get(number, 0)
            self._polled[number] = seen + 1
            if seen >= self.completion_polls_needed:
                pr["status"] = view["status"] = "completed"
        view.pop("_completing", None)
        view["repository"] = {
            "id": self.repository_id,
            "name": self.repository,
            "project": {"id": self.project_id, "name": self.project},
        }
        return view

    def _pulls(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            p = request.url.params
            found = [
                self._view(pr)
                for pr in self.prs.values()
                if pr["sourceRefName"] == p.get("searchCriteria.sourceRefName", pr["sourceRefName"])
                and pr["targetRefName"] == p.get("searchCriteria.targetRefName", pr["targetRefName"])
                and pr["status"] == p.get("searchCriteria.status", pr["status"])
            ]
            return httpx.Response(200, json={"value": found})
        body = json.loads(request.content)
        number = len(self.prs) + 1
        source = self.refs.get(body["sourceRefName"])
        if source is None:
            return _error(400, "TF401398: the source branch does not exist")
        self.prs[number] = {
            "pullRequestId": number,
            "title": body["title"],
            "description": body.get("description", ""),
            "isDraft": body.get("isDraft", False),
            "status": "active",
            "mergeStatus": "succeeded",
            "sourceRefName": body["sourceRefName"],
            "targetRefName": body["targetRefName"],
            "reviewers": [],
            "lastMergeSourceCommit": {"commitId": source},
        }
        self.iterations[number] = [{"id": 1, "sourceRefCommit": {"commitId": source}}]
        return httpx.Response(201, json=self._view(self.prs[number]))

    def _pull(self, request: httpx.Request, number: str) -> httpx.Response:
        pr = self.prs.get(int(number))
        if pr is None:
            return _error(404, "TF401180: the pull request does not exist")
        if request.method == "GET":
            return httpx.Response(200, json=self._view(pr))
        body = json.loads(request.content)
        if body.get("status") == "completed":
            denied = self._complete(pr, body)
            if denied is not None:
                return denied
        for key in ("description", "isDraft"):
            if key in body:
                pr[key] = body[key]
        return httpx.Response(200, json=self._view(pr))

    def _complete(self, pr: dict[str, Any], body: dict[str, Any]) -> httpx.Response | None:
        if body["lastMergeSourceCommit"]["commitId"] != pr["lastMergeSourceCommit"]["commitId"]:
            return _error(409, "TF401181: the source branch changed since the completion was asked")
        options = body.get("completionOptions", {})
        if options.get("mergeStrategy") not in {"squash", "noFastForward", "rebase", "rebaseMerge"}:
            return _error(400, "the merge strategy is unknown")
        blocking = [
            e
            for e in self.evaluations.get(pr["pullRequestId"], [])
            if e["status"] != "approved" and not options.get("bypassPolicy")
        ]
        if blocking:
            return _error(400, "TF401179: the pull request cannot be completed, a policy is not met")
        self.completion = {"pull_request": pr["pullRequestId"], **options}
        pr["_completing"] = True
        return None

    def _thread(self, request: httpx.Request, number: str) -> httpx.Response:
        body = json.loads(request.content)
        threads = self.threads.setdefault(int(number), [])
        threads.append(body)
        return httpx.Response(201, json={"id": len(threads), **body})

    def _iterations(self, request: httpx.Request, number: str) -> httpx.Response:
        return httpx.Response(200, json={"value": self.iterations[int(number)]})

    def _changes(self, request: httpx.Request, number: str, iteration: str) -> httpx.Response:
        pr = self.prs[int(number)]
        source = self.iterations[int(number)][int(iteration) - 1]["sourceRefCommit"]["commitId"]
        target = self.refs[pr["targetRefName"]]
        entries = self._changes_between(target, source)
        skip = int(request.url.params.get("$skip", 0))
        top = int(request.url.params.get("$top", 100))
        page = entries[skip : skip + top]
        more = skip + top < len(entries)
        return httpx.Response(200, json={"changeEntries": page, "nextSkip": skip + top if more else 0})

    def _pr_statuses(self, request: httpx.Request, number: str) -> httpx.Response:
        return httpx.Response(200, json={"value": self.statuses.get(int(number), [])})

    def _pr_labels(self, request: httpx.Request, number: str) -> httpx.Response:
        return httpx.Response(200, json={"value": [{"name": n} for n in self.labels.get(int(number), [])]})

    def _reviewer(self, request: httpx.Request, number: str, identity: str) -> httpx.Response:
        known = {i["id"] for i in self.identities}
        if identity not in known:
            return _error(400, "TF401001: the identity is unknown")
        pr = self.prs[int(number)]
        pr["reviewers"].append(
            {"id": identity, "uniqueName": identity, "vote": json.loads(request.content)["vote"]}
        )
        return httpx.Response(200, json={"id": identity})

    def _policies(self, request: httpx.Request) -> httpx.Response:
        artifact = request.url.params["artifactId"]
        match = re.fullmatch(rf"vstfs:///CodeReview/CodeReviewId/{self.project_id}/(\d+)", artifact)
        if not match or request.url.params["api-version"] != "7.1-preview.1":
            return _error(400, "the artifact id or the preview version is wrong")
        return httpx.Response(200, json={"value": self.evaluations.get(int(match.group(1)), [])})

    # ───── pipelines ─────

    def _builds(self, request: httpx.Request) -> httpx.Response:
        p = request.url.params
        found = [
            b
            for b in reversed(self.builds)
            if (not p.get("repositoryId") or b["repository"]["id"] == p["repositoryId"])
            and (not p.get("branchName") or b["sourceBranch"] == p["branchName"])
            and (not p.get("definitions") or str(b["definition"]["id"]) == p["definitions"])
        ]
        return httpx.Response(200, json={"value": found[: int(p.get("$top", 1000))]})

    def _build(self, request: httpx.Request, build_id: str) -> httpx.Response:
        found = next((b for b in self.builds if b["id"] == int(build_id)), None)
        return httpx.Response(200, json=found) if found else _error(404, "build not found")

    def _timeline(self, request: httpx.Request, build_id: str) -> httpx.Response:
        if int(build_id) not in self.timelines:
            return _error(404, "no timeline")
        return httpx.Response(200, json={"records": self.timelines[int(build_id)]})

    def _log_list(self, request: httpx.Request, build_id: str) -> httpx.Response:
        count = len(self.logs.get(int(build_id), []))
        return httpx.Response(200, json={"value": [{"id": i + 1} for i in range(count)]})

    def _log(self, request: httpx.Request, build_id: str, log_id: str) -> httpx.Response:
        if "text/plain" not in request.headers.get("accept", ""):
            return _error(406, "the log is served as text")
        return httpx.Response(200, text=self.logs[int(build_id)][int(log_id) - 1])

    def _pipeline_list(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": [{"id": i, "name": n} for i, n in self.pipelines.items()]})

    def _run(self, request: httpx.Request, pipeline: str) -> httpx.Response:
        body = json.loads(request.content)
        source = body["resources"]["repositories"]["self"]
        sha = source.get("version") or self.refs.get(source.get("refName", ""), "")
        branch = source.get("refName", "refs/heads/main").removeprefix("refs/heads/")
        build_id = self.add_build(int(pipeline), sha, branch=branch, status="notStarted", result=None)
        self.runs.append({"pipeline": int(pipeline), "id": build_id, **body})
        return httpx.Response(200, json={"id": build_id, "state": "unknown"})


def _strip(path: str, organisation: str) -> str:
    return path.removeprefix(f"/{organisation}")


def _error(status: int, message: str) -> httpx.Response:
    return httpx.Response(status, json={"message": message})
