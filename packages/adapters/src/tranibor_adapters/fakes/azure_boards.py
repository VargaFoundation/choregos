# SPDX-License-Identifier: Apache-2.0
"""A scriptable Azure DevOps Boards server, as an `httpx` transport: no network (S13-07).

It answers the calls `AzureBoardsTracker` makes, from an in-memory organisation: work items with their
fields, tags, relations and comments, the states of a process, the field catalogue and a WIQL endpoint
that understands the clauses the tracker writes. It checks what a real server checks and a loose mock
does not: the `api-version`, the credential (a PAT as Basic with an empty user name, or the bearer token
its own Entra endpoint issued), the JSON Patch content type, and that a state is one of the type's.
It proves the tracker speaks the documented protocol; it does not prove a real organisation accepts it.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any
from urllib.parse import unquote

import httpx

#: The states of the Agile process, with their categories.
AGILE_STATES = [
    {"name": "New", "category": "Proposed"},
    {"name": "Active", "category": "InProgress"},
    {"name": "Resolved", "category": "Resolved"},
    {"name": "Closed", "category": "Completed"},
    {"name": "Removed", "category": "Removed"},
]


class FakeAzureBoardsServer:
    """`transport()` backs a `RestClient`'s `httpx.AsyncClient`; `calls` records each request."""

    def __init__(
        self,
        project: str = "Billing",
        *,
        pat: str = "pat",
        entra_secret: str | None = None,
        collection_path: str = "/northwind",
    ) -> None:
        self.project = project
        #: The organisation's (or the collection's) path in `base_url`, before the project.
        self.collection_path = collection_path
        self.pat = pat
        self.entra_secret = entra_secret
        self.issued_token = "entra-access-token"
        self.items: dict[int, dict[str, Any]] = {}
        self.comments: dict[int, list[dict[str, Any]]] = {}
        self.states = {"Task": AGILE_STATES, "Bug": AGILE_STATES}
        self.field_catalogue = [
            {"name": "Title", "referenceName": "System.Title"},
            {"name": "Cost (€)", "referenceName": "Custom.Cost"},
            {"name": "Size", "referenceName": "Custom.Size"},
            {"name": "Risk", "referenceName": "Custom.Risk"},
            {"name": "Run", "referenceName": "Custom.Run"},
            {"name": "Customer", "referenceName": "Custom.Customer"},
        ]
        self.calls: list[tuple[str, str]] = []
        self.queries: list[str] = []
        self.token_requests: list[dict[str, str]] = []
        self._next = 1

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def seed(
        self, title: str, *, state: str = "New", tags: str = "", kind: str = "Task", **fields: Any
    ) -> int:
        number, self._next = self._next, self._next + 1
        self.items[number] = {
            "id": number,
            "rev": 1,
            "fields": {
                "System.TeamProject": self.project,
                "System.Title": title,
                "System.State": state,
                "System.WorkItemType": kind,
                "System.Tags": tags,
                "System.AreaPath": self.project,
                **fields,
            },
            "relations": [],
        }
        return number

    # ───────────────────────── the protocol ─────────────────────────

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = unquote(request.url.path)
        if path.startswith(self.collection_path + "/"):
            path = path.removeprefix(self.collection_path)
        self.calls.append((request.method, path))
        if path.endswith("/oauth2/v2.0/token"):
            return self._token(request)
        if not self._authorised(request):
            return httpx.Response(401, text="TF400813: the user is not authorized")
        if request.url.params.get("api-version") not in {"7.1", "7.1-preview.4"}:
            return httpx.Response(400, text="VS402337: api-version missing or unsupported")
        if request.method == "GET" and path.endswith("/_apis/projects/" + self.project):
            return httpx.Response(200, json={"name": self.project})
        for pattern, handler in self._routes:
            found = re.fullmatch(pattern, path)
            if found and request.method == handler[0]:
                response: httpx.Response = handler[1](self, request, *found.groups())
                return response
        return httpx.Response(404, text=f"no such route {request.method} {path}")

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = dict(item.split("=", 1) for item in request.content.decode().split("&"))
        self.token_requests.append({k: unquote(v.replace("+", " ")) for k, v in form.items()})
        if unquote(form.get("client_secret", "")) != self.entra_secret:
            return httpx.Response(401, json={"error": "invalid_client"})
        return httpx.Response(200, json={"access_token": self.issued_token, "expires_in": 3599})

    def _authorised(self, request: httpx.Request) -> bool:
        header = request.headers.get("authorization", "")
        if header == f"Bearer {self.issued_token}" and self.entra_secret is not None:
            return True
        expected = "Basic " + base64.b64encode(f":{self.pat}".encode()).decode()
        return bool(header == expected)

    def _get_item(self, request: httpx.Request, number: str) -> httpx.Response:
        item = self.items.get(int(number))
        if item is None:
            return httpx.Response(404, text="TF401232: work item does not exist")
        wanted = request.url.params.get("fields")
        if wanted:
            keep = set(wanted.split(","))
            return httpx.Response(
                200, json={**item, "fields": {k: v for k, v in item["fields"].items() if k in keep}}
            )
        return httpx.Response(200, json=item)

    def _patch_item(self, request: httpx.Request, number: str) -> httpx.Response:
        if request.headers.get("content-type", "").split(";")[0] != "application/json-patch+json":
            return httpx.Response(415, text="the body must be application/json-patch+json")
        item = self.items.get(int(number))
        if item is None:
            return httpx.Response(404, text="TF401232: work item does not exist")
        known = {f["referenceName"] for f in self.field_catalogue} | set(item["fields"])
        for operation in json.loads(request.content):
            path = str(operation["path"])
            if path.startswith("/fields/"):
                name = path.removeprefix("/fields/")
                if name not in known:
                    return httpx.Response(400, text=f"TF51535: field {name} does not exist")
                if name == "System.State" and operation["value"] not in {
                    s["name"] for s in self.states[item["fields"]["System.WorkItemType"]]
                }:
                    return httpx.Response(400, text="TF401320: the state is not in the type's workflow")
                item["fields"][name] = operation["value"]
            elif path == "/relations/-":
                if operation["value"] in item["relations"]:
                    return httpx.Response(400, text="VS403355: Relation already exists")
                item["relations"].append(operation["value"])
        item["rev"] += 1
        return httpx.Response(200, json=item)

    def _create(self, request: httpx.Request, kind: str) -> httpx.Response:
        number = self.seed("", kind=kind)
        response = self._patch_item(request, str(number))
        if response.status_code >= 400:
            del self.items[number]
        return response

    def _states(self, request: httpx.Request, kind: str) -> httpx.Response:
        return httpx.Response(200, json={"value": self.states.get(kind, [])})

    def _fields(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": self.field_catalogue})

    def _comments(self, request: httpx.Request, number: str) -> httpx.Response:
        if request.url.params.get("api-version") != "7.1-preview.4":
            return httpx.Response(400, text="comments exist only as 7.1-preview.4")
        thread = self.comments.setdefault(int(number), [])
        if request.method == "GET":
            return httpx.Response(200, json={"count": len(thread), "comments": thread})
        comment = {
            "id": len(thread) + 1,
            "text": json.loads(request.content)["text"],
            "createdBy": {"displayName": "Tranibor"},
        }
        thread.append(comment)
        return httpx.Response(200, json=comment)

    def _edit_comment(self, request: httpx.Request, number: str, comment_id: str) -> httpx.Response:
        for comment in self.comments.get(int(number), []):
            if str(comment["id"]) == comment_id:
                comment["text"] = json.loads(request.content)["text"]
                return httpx.Response(200, json=comment)
        return httpx.Response(404, text="comment not found")

    def _wiql(self, request: httpx.Request) -> httpx.Response:
        """Understands what the tracker writes: the project, the area, `Tags CONTAINS`, the type and the
        state, and the Completed/Removed exclusion. The query text is kept in `queries` for assertions."""
        query = str(json.loads(request.content)["query"])
        self.queries.append(query)
        categories = {s["name"]: s["category"] for s in AGILE_STATES}
        tags = re.findall(r"\[System.Tags\] CONTAINS '((?:[^']|'')*)'", query)
        found = []
        for number, item in sorted(self.items.items(), reverse=True):
            fields = item["fields"]
            have = {t.strip() for t in str(fields["System.Tags"]).split(";")}
            if categories.get(fields["System.State"]) in {"Completed", "Removed"}:
                continue
            if tags and not any(t.replace("''", "'") in have for t in tags):
                continue
            found.append({"id": number})
        return httpx.Response(200, json={"workItems": found})

    _routes: list[tuple[str, tuple[str, Any]]]


FakeAzureBoardsServer._routes = [
    (r"/[^/]+/_apis/wit/workitems/(\d+)", ("GET", FakeAzureBoardsServer._get_item)),
    (r"/[^/]+/_apis/wit/workitems/(\d+)", ("PATCH", FakeAzureBoardsServer._patch_item)),
    (r"/[^/]+/_apis/wit/workitems/\$(\w+)", ("POST", FakeAzureBoardsServer._create)),
    (r"/[^/]+/_apis/wit/workitemtypes/(\w+)/states", ("GET", FakeAzureBoardsServer._states)),
    (r"/[^/]+/_apis/wit/fields", ("GET", lambda self, request: self._fields(request))),
    (r"/[^/]+/_apis/wit/workItems/(\d+)/comments", ("GET", FakeAzureBoardsServer._comments)),
    (r"/[^/]+/_apis/wit/workItems/(\d+)/comments", ("POST", FakeAzureBoardsServer._comments)),
    (r"/[^/]+/_apis/wit/workItems/(\d+)/comments/(\d+)", ("PATCH", FakeAzureBoardsServer._edit_comment)),
    (r"/[^/]+/_apis/wit/wiql", ("POST", lambda self, request: self._wiql(request))),
]
