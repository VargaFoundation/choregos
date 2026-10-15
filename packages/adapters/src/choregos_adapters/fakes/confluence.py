# SPDX-License-Identifier: Apache-2.0
"""A Confluence Cloud site in memory (ADR 0005), scripted by tests: spaces, pages with versions,
restrictions, attachments, and the REST API v2 over an `httpx.MockTransport`. It checks the Basic
credentials, serves a scoped token through `api.atlassian.com/ex/confluence/{cloud_id}`, redirects
attachment downloads to a media host, and can be told to fail."""

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

    def delete_page(self, id: str) -> None:
        self.pages[id].deleted = True

    def restrict(self, id: str, restricted: bool = True) -> None:
        """Hides the page from the account: not listed, 403 when read."""
        self.pages[id].restricted = restricted

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

    def _paged(self, path: str, rows: list[Any], params: Any, render: Any) -> httpx.Response:
        size = min(int(params.get("limit", 250)), self.page_size)
        start = int(params.get("cursor", 0))
        links: dict[str, str] = {}
        if start + size < len(rows):
            links["next"] = f"{path}?cursor={start + size}&limit={size}&status=current"
        return httpx.Response(
            200, json={"results": [render(r) for r in rows[start : start + size]], "_links": links}
        )

    @staticmethod
    def _summary(page: _Page) -> dict[str, Any]:
        return {
            "id": page.id, "title": page.title, "parentId": page.parent, "status": "current",
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
