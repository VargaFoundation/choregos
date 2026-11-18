# SPDX-License-Identifier: Apache-2.0
"""A Confluence Cloud site in memory (ADR 0005), scripted by tests: spaces, pages with versions,
folders, restrictions, attachments, and the REST API v2 over an `httpx.MockTransport`. It checks the Basic
credentials, serves a scoped token through `api.atlassian.com/ex/confluence/{cloud_id}`, redirects
attachment downloads to a media host, and can be told to fail.

Folders are what Confluence Cloud returns: content of type `folder`, never in a space's page
listing, the parent (`parentId`, `parentType: "folder"`) of the pages filed in them, read by
`GET /folders/{id}` and walked by the `descendants` endpoints with the real defaults (`depth` 2,
at most 10; `limit` 25, at most 250)."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

SITE_HOST = "fake.atlassian.net"
GATEWAY_HOST = "api.atlassian.com"
MEDIA_HOST = "media.fake"
CLOUD_ID = "11111111-2222-3333-4444-555555555555"


@dataclass
class _Page:
    id: str
    space: str
    title: str
    body: str
    parent: str | None
    version: int = 1
    labels: list[str] = field(default_factory=list)
    restricted: bool = False
    deleted: bool = False
    storage_only: bool = False


@dataclass
class _Folder:
    id: str
    space: str
    title: str
    parent: str | None
    restricted: bool = False


@dataclass
class _Attachment:
    id: str
    page: str
    title: str
    data: bytes
    media_type: str
    version: int = 1


class FakeConfluence:
    def __init__(self, *, email: str = "bot@example.test", token: str = "fake-token") -> None:
        self.email, self.token = email, token
        self.page_size = 250
        self.spaces: dict[str, str] = {}
        self.pages: dict[str, _Page] = {}
        #: content ids are shared: a folder id is never a page id
        self.folders: dict[str, _Folder] = {}
        self.attachments: dict[str, _Attachment] = {}
        #: every request served, in order: tests read the headers and URLs here
        self.requests: list[httpx.Request] = []
        self._failures: list[tuple[str, int, int, dict[str, str]]] = []

    # -- scripting ----------------------------------------------------------------------------

    def add_space(self, key: str) -> str:
        self.spaces[key] = str(65536 + len(self.spaces) + 1)
        return self.spaces[key]

    def put_page(
        self,
        space: str,
        id: str,
        title: str,
        body_html: str,
        parent: str | None = None,
        *,
        labels: list[str] | None = None,
        storage_only: bool = False,
    ) -> None:
        """Creates a page, or edits it: its version goes up."""
        if id in self.pages:
            page = self.pages[id]
            page.title, page.body, page.parent = title, body_html, parent
            page.version += 1
            page.deleted = False
        else:
            self.pages[id] = _Page(id, space, title, body_html, parent)
        self.pages[id].storage_only = storage_only
        if labels is not None:
            self.pages[id].labels = labels

    def put_folder(self, space: str, id: str, title: str, parent: str | None = None) -> None:
        """Creates a folder, or moves and renames it; `parent` is a page or a folder."""
        self.folders[id] = _Folder(id, space, title, parent)

    def delete_page(self, id: str) -> None:
        self.pages[id].deleted = True

    def restrict(self, id: str, restricted: bool = True) -> None:
        """Hides the page (or the folder) from the account: not listed, 403 when read."""
        (self.pages.get(id) or self.folders[id]).restricted = restricted

    def add_attachment(
        self, page: str, id: str, title: str, data: bytes, media_type: str = "application/pdf"
    ) -> None:
        existing = self.attachments.get(id)
        self.attachments[id] = _Attachment(
            id, page, title, data, media_type, (existing.version + 1) if existing else 1
        )

    def fail_next(self, path: str, status: int, times: int = 1, *, retry_after: str | None = None) -> None:
        """The next `times` requests whose path contains `path` answer `status`."""
        self._failures.append((path, status, times, {"Retry-After": retry_after} if retry_after else {}))

    def fetched(self, id: str) -> int:
        """How many times a page's body was requested."""
        return sum(
            1 for r in self.requests if r.url.path.endswith(f"/pages/{id}") and "body-format" in str(r.url)
        )

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    # -- the API ------------------------------------------------------------------------------

    def _authorised(self, request: httpx.Request) -> bool:
        expected = base64.b64encode(f"{self.email}:{self.token}".encode()).decode()
        return bool(request.headers.get("Authorization") == f"Basic {expected}")

    def _visible(self, space: str) -> list[_Page]:
        return [p for p in self.pages.values() if p.space == space and not p.deleted and not p.restricted]

    def _parent_type(self, parent: str | None) -> str | None:
        if parent is None:
            return None
        return "folder" if parent in self.folders else "page"

    def _shown(self, id: str) -> _Page | _Folder | None:
        """A page or a folder the account sees, or `None`."""
        node: _Page | _Folder | None = self.pages.get(id) or self.folders.get(id)
        if node is None or node.restricted or (isinstance(node, _Page) and node.deleted):
            return None
        return node

    def _scripted_failure(self, path: str) -> httpx.Response | None:
        for index, (needle, status, times, headers) in enumerate(self._failures):
            if needle in path:
                self._failures[index] = (needle, status, times - 1, headers)
                if times - 1 <= 0:
                    del self._failures[index]
                return httpx.Response(status, headers=headers, json={"message": "scripted failure"})
        return None

    def _handle(self, request: httpx.Request) -> httpx.Response:  # noqa: C901 - one route per endpoint
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        if host == MEDIA_HOST:
            att = self.attachments[path.strip("/")]
            return httpx.Response(200, content=att.data, headers={"Content-Type": att.media_type})
        scripted = self._scripted_failure(path)
        if scripted is not None:
            return scripted
        if path == "/_edge/tenant_info":
            return httpx.Response(200, json={"cloudId": CLOUD_ID})
        if host == GATEWAY_HOST:
            prefix = f"/ex/confluence/{CLOUD_ID}"
            if not path.startswith(prefix):
                return httpx.Response(404, json={"message": "unknown cloud id"})
            path = path[len(prefix) :]
        elif host != SITE_HOST:
            return httpx.Response(404, json={"message": f"unknown host {host}"})
        if not self._authorised(request):
            return httpx.Response(401, json={"message": "Unauthorized"})
        params = request.url.params
        if path == "/wiki/api/v2/spaces":
            keys = params.get_list("keys")
            found = [
                {"id": sid, "key": key, "name": key}
                for key, sid in self.spaces.items()
                if not keys or key in keys
            ]
            return httpx.Response(200, json={"results": found, "_links": {}})
        if match := re.fullmatch(r"/wiki/api/v2/spaces/(\d+)/pages", path):
            key = next((k for k, sid in self.spaces.items() if sid == match.group(1)), None)
            if key is None:
                return httpx.Response(404, json={})
            return self._paged(
                path, sorted(self._visible(key), key=lambda p: int(p.id)), params, self._summary
            )
        if match := re.fullmatch(r"/wiki/api/v2/(pages|folders)/([^/]+)/descendants", path):
            return self._descendants(path, match.group(1), match.group(2), params)
        if match := re.fullmatch(r"/wiki/api/v2/folders/([^/]+)", path):
            return self._folder(match.group(1))
        if match := re.fullmatch(r"/wiki/api/v2/pages/([^/]+)", path):
            return self._page(match.group(1), params.get("body-format"))
        if match := re.fullmatch(r"/wiki/api/v2/pages/([^/]+)/labels", path):
            page = self.pages.get(match.group(1))
            if page is None or page.deleted or page.restricted:
                return httpx.Response(404, json={})
            labels = [{"id": str(i), "name": name, "prefix": "global"} for i, name in enumerate(page.labels)]
            return self._paged(path, labels, params, lambda row: row)
        if match := re.fullmatch(r"/wiki/api/v2/pages/([^/]+)/attachments", path):
            held = sorted(
                (a for a in self.attachments.values() if a.page == match.group(1)), key=lambda a: a.id
            )
            return self._paged(path, held, params, self._attachment)
        if path.startswith("/wiki/download/attachments/"):
            target = next((a for a in self.attachments.values() if path.endswith("/" + a.title)), None)
            if target is None:
                return httpx.Response(404, json={})
            return httpx.Response(
                302, headers={"Location": f"https://{MEDIA_HOST}/{target.id}?signature=fake"}
            )
        return httpx.Response(404, json={"message": f"not served: {path}"})

    def _paged(
        self, path: str, rows: list[Any], params: Any, render: Any, *, default_limit: int = 250
    ) -> httpx.Response:
        size = min(int(params.get("limit", default_limit)), self.page_size)
        start = int(params.get("cursor", 0))
        links: dict[str, str] = {}
        if start + size < len(rows):
            # The next link keeps the request's other parameters (`depth`, `status`), as Confluence's does.
            following = params.set("cursor", str(start + size)).set("limit", str(size))
            links["next"] = f"{path}?{following}"
        return httpx.Response(
            200, json={"results": [render(r) for r in rows[start : start + size]], "_links": links}
        )

    def _descendants(self, path: str, kind: str, id: str, params: Any) -> httpx.Response:
        """Everything under a page or a folder the account sees, top to bottom (by depth), pages
        and folders alike, in the minimal `DescendantsResponse` shape."""
        node = self._shown(id)
        if node is None or isinstance(node, _Folder) != (kind == "folders"):
            return httpx.Response(404, json={"message": f"no such {kind[:-1]}"})
        depth = int(params.get("depth", 2))
        if not 1 <= depth <= 10 or int(params.get("limit", 25)) > 250:
            return httpx.Response(400, json={"message": "depth is 1 to 10, limit at most 250"})
        rows: list[dict[str, Any]] = []
        nodes: list[_Page | _Folder] = [*self.pages.values(), *self.folders.values()]
        level, current = 1, [id]
        while current and level <= depth:
            below: list[str] = []
            for parent in current:
                children = sorted(
                    (n for n in nodes if n.parent == parent),
                    key=lambda n: int(n.id),
                )
                for position, child in enumerate(c for c in children if self._shown(c.id)):
                    rows.append({
                        "id": child.id, "status": "current", "title": child.title,
                        "type": "folder" if isinstance(child, _Folder) else "page",
                        "parentId": parent, "depth": level, "childPosition": position,
                    })  # fmt: skip
                    below.append(child.id)
            level, current = level + 1, below
        return self._paged(path, rows, params, lambda row: row, default_limit=25)

    def _folder(self, id: str) -> httpx.Response:
        folder = self.folders.get(id)
        if folder is None:
            return httpx.Response(404, json={"message": "no such folder"})
        if folder.restricted:
            return httpx.Response(403, json={"message": "restricted"})
        return httpx.Response(200, json={
            "id": folder.id, "type": "folder", "status": "current", "title": folder.title,
            "parentId": folder.parent, "parentType": self._parent_type(folder.parent), "position": 0,
            "spaceId": self.spaces[folder.space], "createdAt": "2026-10-15T00:00:00.000Z",
            "version": {"number": 1, "createdAt": "2026-10-15T00:00:00.000Z"},
            "_links": {"webui": f"/spaces/{folder.space}/folder/{folder.id}"},
        })  # fmt: skip

    def _summary(self, page: _Page) -> dict[str, Any]:
        return {
            "id": page.id, "title": page.title, "parentId": page.parent,
            "parentType": self._parent_type(page.parent), "status": "current",
            "version": {"number": page.version, "createdAt": "2026-10-15T00:00:00.000Z"},
            "_links": {"webui": f"/spaces/{page.space}/pages/{page.id}/{page.title.replace(' ', '+')}"},
        }  # fmt: skip

    @staticmethod
    def _attachment(att: _Attachment) -> dict[str, Any]:
        return {
            "id": att.id, "title": att.title, "mediaType": att.media_type, "fileSize": len(att.data),
            "version": {"number": att.version},
            "downloadLink": f"/download/attachments/{att.page}/{att.title}",
        }  # fmt: skip

    def _page(self, id: str, body_format: str | None) -> httpx.Response:
        page = self.pages.get(id)
        if page is None or page.deleted:
            return httpx.Response(404, json={"message": "no such page"})
        if page.restricted:
            return httpx.Response(403, json={"message": "restricted"})
        out = self._summary(page)
        if body_format == "export_view" and not page.storage_only:
            out["body"] = {"export_view": {"representation": "export_view", "value": page.body}}
        elif body_format == "storage" or (body_format == "export_view" and page.storage_only):
            out["body"] = (
                {"storage": {"representation": "storage", "value": page.body}}
                if (body_format == "storage")
                else {}
            )
        return httpx.Response(200, json=out)
