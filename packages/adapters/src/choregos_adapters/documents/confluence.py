# SPDX-License-Identifier: Apache-2.0
"""A Confluence Cloud space, or one page and everything under it, as a knowledge source (S39-20, ADR 0051).

The connector's account is a service account holding **read** on the chosen spaces only. The source
mirrors what that account can read: a page restricted against it is not listed, and leaves the base.

Each sync lists the space once (`/wiki/api/v2/spaces/{id}/pages`, no body, cursor pagination). The
sub-tree of a `page_id` comes from `/wiki/api/v2/pages/{id}/descendants` (ten levels per call): a
page filed in a **folder** has the folder as its `parentId`, and folders are never in the space's
page listing, so a tree rebuilt from that listing alone stopped at the first folder. A folder is not
a document (it has no body); its title is in the path of the pages under it, and a folder that only
the path needs is read once (`/wiki/api/v2/folders/{id}`).

The listing is `full`, so what it no longer holds (deleted, archived, moved out of the tree, newly
restricted) is deleted from the base, and the page `version.number` is the item version: the sync
engine fetches a body only when it changed. A listing that fails part-way **raises**: a partial
listing would read as deletions.

Credentials: an Atlassian API token with the account's email, as HTTP Basic. A *scoped* token must
go through the platform gateway (`api.atlassian.com/ex/confluence/{cloud_id}`). OAuth 2.0 (3LO) is
a follow-up (S39-20c). The Authorization header is never sent to another host: an attachment
download follows Confluence's redirect to its media host by hand, without it.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import re
import unicodedata
from collections import deque
from datetime import UTC, datetime
from typing import Any, NamedTuple
from urllib.parse import urlsplit

import httpx

from ..errors import ConfigurationError, UpstreamError
from ..http.egress import guarded
from ..http.rest import RestClient
from .base import Changes, ItemUnreadableError, SourceItem, SourceRefusedError

#: Pages kept per sync. Over that, the source names a `page_id`.
MAX_PAGES = 5000
#: Pages read from the space listing before giving up, whatever the sub-tree asked for.
MAX_LISTED = 20000
GATEWAY = "https://api.atlassian.com"
#: Set by tests to a `httpx.MockTransport`; `None` in production (same seam as `GraphDriveSource`).
TRANSPORT: httpx.AsyncBaseTransport | None = None
#: Attachments worth converting (the formats of `choregos_api.knowledge.formats`).
ATTACHMENT_SUFFIXES = (".pdf", ".docx", ".pptx", ".xlsx", ".md", ".txt", ".html")
_SITE = re.compile(r"^https://[a-z0-9-]+\.atlassian\.net/?$")
#: The deepest the `descendants` endpoint reaches in one call (its `depth` maximum).
DESCENDANT_DEPTH = 10
#: The content types that can hold pages, and their path in the API: each has a `descendants` endpoint.
_CONTAINERS = {
    "page": "pages",
    "folder": "folders",
    "whiteboard": "whiteboards",
    "database": "databases",
    "embed": "embeds",
}
#: How far up a page's path is followed.
_TRAIL_DEPTH = 20


class _Node(NamedTuple):
    """A page or another piece of the content tree (a folder), as much as its path needs."""

    title: str
    parent: str | None
    parent_type: str | None


def slug(title: str) -> str:
    """A file-name-safe form of a page title."""
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-")[:60].strip("-") or "page"


_PARAMETER = re.compile(r"<ac:parameter\b[^>]*>.*?</ac:parameter>", re.S)
_LINK = re.compile(r"<ac:link\b[^>]*>(.*?)</ac:link>", re.S)
_LINK_BODY = re.compile(r"<ac:(?:plain-text-)?link-body\b[^>]*>(.*?)</ac:(?:plain-text-)?link-body>", re.S)
_REF_TITLE = re.compile(r"ri:(?:content-title|filename|space-key)=\"([^\"]*)\"")
_CODE = re.compile(r"<ac:plain-text-body\b[^>]*>\s*<!\[CDATA\[(.*?)\]\]>\s*</ac:plain-text-body>", re.S)
_ATTACHMENT = re.compile(r"<ri:attachment\b[^>]*ri:filename=\"([^\"]*)\"[^>]*/?>", re.S)
_TAG = re.compile(r"</?(?:ac|ri):[^>]*>")
_CDATA = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.S)


def confluence_storage_to_html(xhtml: str) -> str:
    """Storage-format XHTML with its macros reduced to their text: the body of a macro is kept, its
    parameters are dropped, a link to a page becomes its text, a code macro becomes `<pre>`."""
    text = _PARAMETER.sub("", xhtml)
    text = _CODE.sub(lambda m: f"<pre>{html.escape(m.group(1))}</pre>", text)

    def link(match: re.Match[str]) -> str:
        inner = match.group(1)
        body = _LINK_BODY.search(inner)
        if body:
            return _CDATA.sub(lambda m: html.escape(m.group(1)), _TAG.sub("", body.group(1)))
        ref = _REF_TITLE.search(inner)
        return html.escape(html.unescape(ref.group(1))) if ref else ""

    text = _LINK.sub(link, text)
    text = _ATTACHMENT.sub(lambda m: html.escape(html.unescape(m.group(1))), text)
    text = _CDATA.sub(lambda m: html.escape(m.group(1)), text)
    return _TAG.sub("", text)


def _id(value: Any) -> str | None:
    """A content id as a string (the API sends strings; a number is tolerated), `None` when absent."""
    return str(value) if value else None


def _document(title: str, trail: list[str], body: str, url: str) -> bytes:
    path = f"<p>Path: {html.escape(' / '.join(trail))}</p>" if trail else ""
    source = f'<p>Source: <a href="{html.escape(url, quote=True)}">{html.escape(url)}</a></p>'
    page = (
        f"<html><head><title>{html.escape(title)}</title></head>"
        f"<body><h1>{html.escape(title)}</h1>{path}{body}{source}</body></html>"
    )
    return page.encode()


class ConfluenceSource:
    def __init__(  # noqa: PLR0913 - keyword-only: the site and its account, then what to read
        self,
        *,
        base_url: str,
        email: str,
        api_token: str,
        space_key: str,
        page_id: str | None = None,
        page_ids: list[str] | None = None,
        descendants: bool = True,
        include_root: bool = True,
        include_attachments: bool = False,
        labels: list[str] | None = None,
        auth: str = "basic",
        cloud_id: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not _SITE.match(base_url):
            raise SourceRefusedError(
                f"not an Atlassian Cloud site (https://<site>.atlassian.net): {base_url}"
            )
        if auth == "scoped" and not cloud_id:
            raise ConfigurationError(
                "a scoped token goes through the gateway: set `cloud_id` (test the connector)"
            )
        self.site = base_url.rstrip("/")
        self.space_key, self.page_id = space_key, page_id
        #: The pages chosen one by one (S42-09d); `page_id` is the one-page spelling of the same thing.
        self.roots = [str(p) for p in (page_ids or ([page_id] if page_id else []))]
        self.descendants = descendants
        self.include_root, self.include_attachments = include_root, include_attachments
        self.labels = {label.lower() for label in labels or []}
        # The gateway root for a scoped token, the site itself otherwise: `/wiki/api/v2` hangs off it.
        self.root = f"{GATEWAY}/ex/confluence/{cloud_id}" if auth == "scoped" else self.site
        self._auth = (email, api_token)
        self._client = httpx.AsyncClient(timeout=60.0, transport=guarded(transport or TRANSPORT))
        self._rest = RestClient(self.root, auth=self._auth, client=self._client, service="confluence")
        self._trails: dict[str, list[str]] = {}
        #: Every page and folder met in this sync, by id; `None` for a folder the account cannot read.
        self._nodes: dict[str, _Node | None] = {}
        self._titles: dict[str, str] = {}
        self._downloads: dict[str, str] = {}

    def close(self) -> None:
        """Called by the sync engine without `await`: the client closes in the background."""
        with contextlib.suppress(RuntimeError):  # no loop: nothing is open
            asyncio.get_running_loop().create_task(self._client.aclose())

    # -- requests -----------------------------------------------------------------------------

    def _url(self, link: str) -> str:
        """A path or a `_links.next` of the API, as an absolute URL on the site or the gateway."""
        if link.startswith("http"):
            return link
        if link.startswith("/wiki"):
            return self.root + link
        return f"{urlsplit(self.root).scheme}://{urlsplit(self.root).netloc}{link}"

    async def _get(self, link: str, **params: Any) -> dict[str, Any]:
        return dict(await self._rest.request("GET", self._url(link), params=params or None))

    async def _listing(self, link: str, **params: Any) -> list[dict[str, Any]]:
        """Every result of a paginated listing; an error part-way raises."""
        results: list[dict[str, Any]] = []
        page = await self._get(link, **params)
        while True:
            results.extend(page.get("results", []))
            if len(results) > MAX_LISTED:
                raise SourceRefusedError(f"more than {MAX_LISTED} entries in {link}: name a page_id")
            following = (page.get("_links") or {}).get("next")
            if not following:
                return results
            page = await self._get(following)

    def _web(self, webui: str | None) -> str | None:
        return f"{self.site}/wiki{webui}" if webui else None

    # -- listing ------------------------------------------------------------------------------

    async def _selected(self) -> list[dict[str, Any]]:
        spaces = (await self._get("/wiki/api/v2/spaces", keys=self.space_key)).get("results", [])
        if not spaces:
            raise SourceRefusedError(
                f"space `{self.space_key}` does not exist, or its account cannot read it"
            )
        pages = await self._listing(
            f"/wiki/api/v2/spaces/{spaces[0]['id']}/pages", status="current", limit=250
        )
        by_id = {str(p["id"]): p for p in pages}
        self._titles = {pid: str(p.get("title", pid)) for pid, p in by_id.items()}
        self._nodes = {
            pid: _Node(self._titles[pid], _id(p.get("parentId")), p.get("parentType"))
            for pid, p in by_id.items()
        }
        chosen: list[str]
        if not self.roots:
            chosen = list(by_id)
        else:
            for root in self.roots:
                if root not in by_id:
                    raise SourceRefusedError(
                        f"page {root} is not in space `{self.space_key}`, or its account cannot read it"
                    )
            chosen = await self._tree(by_id)
            if not self.include_root:
                chosen = [pid for pid in chosen if pid not in self.roots]
        if len(chosen) > MAX_PAGES:
            raise SourceRefusedError(f"{len(chosen)} pages, more than {MAX_PAGES}: name a page_id")
        for pid in chosen:
            self._trails[pid] = await self._trail(pid)
        return [by_id[pid] for pid in chosen]

    async def _tree(self, by_id: dict[str, dict[str, Any]]) -> list[str]:
        """The chosen pages and, with `descendants`, every listed page under them, through folders."""
        children: dict[str, list[str]] = {}
        if self.descendants:
            reached: set[str] = set()
            for root in self.roots:
                if root in reached:
                    continue  # already under another chosen page: its descendants are known
                for entry in await self._descendants(root):
                    eid, parent = str(entry["id"]), _id(entry.get("parentId"))
                    reached.add(eid)
                    if entry.get("status", "current") != "current" or not parent:
                        continue  # archived: neither listed nor crossed
                    if entry.get("type") != "page":
                        self._nodes.setdefault(eid, _Node(str(entry.get("title", eid)), parent, None))
                    elif eid not in by_id:
                        continue  # a page the listing does not hold: not crossed, as before
                    children.setdefault(parent, []).append(eid)
        chosen: list[str] = []
        seen: set[str] = set()
        queue = deque(self.roots)
        while queue:
            current = queue.popleft()
            if current in seen:
                continue  # a chosen page under another chosen page is listed once
            seen.add(current)
            if current in by_id:
                chosen.append(current)  # a folder is crossed, not listed: it has no body
            queue.extend(children.get(current, []))
        return chosen

    async def _descendants(self, root: str) -> list[dict[str, Any]]:
        """Everything under a page, folders included, top to bottom. One call reaches ten levels;
        the tree goes on from each entry of the tenth, through the endpoint of its type."""
        found: list[dict[str, Any]] = []
        queue = deque([("page", root)])
        while queue:
            kind, cid = queue.popleft()
            batch = await self._listing(
                f"/wiki/api/v2/{_CONTAINERS[kind]}/{cid}/descendants",
                limit=250,
                depth=DESCENDANT_DEPTH,
            )
            found.extend(batch)
            if len(found) > MAX_LISTED:
                raise SourceRefusedError(f"more than {MAX_LISTED} entries under page {root}")
            queue.extend(
                (str(d["type"]), str(d["id"]))
                for d in batch
                if d.get("depth") == DESCENDANT_DEPTH and d.get("type") in _CONTAINERS
            )
        return found

    async def _trail(self, pid: str) -> list[str]:
        """The titles above a page, top first: pages and folders, up to the space."""
        trail: list[str] = []
        node = self._nodes[pid]
        while node and node.parent and len(trail) < _TRAIL_DEPTH:
            if node.parent not in self._nodes and node.parent_type == "folder":
                self._nodes[node.parent] = await self._folder(node.parent)
            node = self._nodes.get(node.parent)
            if node:
                trail.append(node.title)
        return list(reversed(trail))

    async def _folder(self, fid: str) -> _Node | None:
        """A folder in a page's path, read once per sync. One the account cannot read, or a scoped
        token without `read:folder:confluence` (401 on this call alone), ends the path there: a
        path is not worth failing the sync."""
        try:
            folder = await self._get(f"/wiki/api/v2/folders/{fid}")
        except UpstreamError as error:
            if error.status_code in {401, 403, 404}:
                return None
            raise
        return _Node(str(folder.get("title", fid)), _id(folder.get("parentId")), folder.get("parentType"))

    async def _labelled(self, pid: str) -> bool:
        found = await self._listing(f"/wiki/api/v2/pages/{pid}/labels", limit=250)
        return bool({str(label.get("name", "")).lower() for label in found} & self.labels)

    async def changes(self, cursor: dict[str, Any] | None) -> Changes:
        pages = await self._selected()
        if self.labels:
            pages = [p for p in pages if await self._labelled(str(p["id"]))]
        items = [
            SourceItem(
                ref=f"page:{p['id']}",
                name=f"{slug(str(p.get('title', '')))}-{p['id']}.html",
                version=str((p.get("version") or {}).get("number", "")),
                url=self._web((p.get("_links") or {}).get("webui")),
            )
            for p in pages
        ]
        if self.include_attachments:
            for page in pages:
                items.extend(await self._attachments(page))
        # The cursor is informative only: page versions are the delta, there is no time window to drift.
        return Changes(
            items=items,
            cursor={"listed_at": datetime.now(UTC).isoformat(), "pages": len(pages)},
            full=True,
        )

    async def _attachments(self, page: dict[str, Any]) -> list[SourceItem]:
        found = await self._listing(f"/wiki/api/v2/pages/{page['id']}/attachments", limit=250)
        for a in found:
            self._downloads[f"att:{a['id']}"] = str(a.get("downloadLink", ""))
        return [
            SourceItem(
                ref=f"att:{a['id']}",
                name=str(a.get("title", a["id"])),
                version=str((a.get("version") or {}).get("number", "")),
                url=self._web((a.get("_links") or {}).get("webui"))
                or self._web((page.get("_links") or {}).get("webui")),
                size=a.get("fileSize"),
            )
            for a in found
            if str(a.get("title", "")).lower().endswith(ATTACHMENT_SUFFIXES)
        ]

    # -- fetching -----------------------------------------------------------------------------

    async def fetch(self, item: SourceItem) -> bytes:
        try:
            if item.ref.startswith("att:"):
                return await self._download(item)
            return await self._page(item)
        except UpstreamError as error:
            if error.status_code in {403, 404, 410}:
                # Gone or restricted since the listing: this one is skipped, the next listing says it.
                raise ItemUnreadableError(f"{item.name}: {error.status_code}") from error
            raise

    async def _page(self, item: SourceItem) -> bytes:
        pid = item.ref.split(":", 1)[1]
        page = await self._body(pid, "export_view")
        body = ((page.get("body") or {}).get("export_view") or {}).get("value")
        if not body:
            page = await self._body(pid, "storage")
            body = confluence_storage_to_html(
                str(((page.get("body") or {}).get("storage") or {}).get("value", ""))
            )
        title = str(page.get("title") or self._titles.get(pid, pid))
        return _document(title, self._trails.get(pid, []), str(body), item.url or "")

    async def _body(self, pid: str, fmt: str) -> dict[str, Any]:
        try:
            return await self._get(f"/wiki/api/v2/pages/{pid}", **{"body-format": fmt})
        except UpstreamError as error:
            if fmt == "export_view" and error.status_code == 400:
                return {"title": None, "body": {}}  # this site does not render it: use storage
            raise

    async def _download(self, item: SourceItem) -> bytes:
        link = self._downloads.get(item.ref)
        if not link:
            raise ItemUnreadableError(f"{item.name}: no download link")
        response = await self._client.get(self._url(f"/wiki{link}"), auth=self._auth)
        hops = 0
        while response.is_redirect and hops < 5:
            # The media host is another origin: it is reached with its own signed URL, no credentials.
            target = str(response.next_request.url) if response.next_request else response.headers["location"]
            response = await self._client.get(target)
            hops += 1
        if response.status_code >= 400:
            raise UpstreamError("confluence", f"download of {item.name} → {response.status_code}",
                                status_code=response.status_code)  # fmt: skip
        return response.content


class ConfluenceDocumentsConnector:
    """The organisation's `documents/confluence` connector: one service account (email + API token),
    from which each knowledge source opens the space or page tree it names."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def source(self, config: dict[str, Any], *, key: str) -> ConfluenceSource:
        return ConfluenceSource(
            base_url=str(self.config["base_url"]),
            email=str(self.config["email"]),
            api_token=str(self.config.get("api_token", "")),
            auth=str(self.config.get("auth", "basic")),
            cloud_id=self.config.get("cloud_id"),
            space_key=str(config["space_key"]),
            page_id=str(config["page_id"]) if config.get("page_id") else None,
            page_ids=[str(p) for p in config.get("page_ids") or []],
            descendants=bool(config.get("descendants", True)),
            include_root=bool(config.get("include_root", True)),
            include_attachments=bool(config.get("include_attachments", False)),
            labels=list(config.get("labels") or []),
        )

    async def test(self) -> dict[str, Any]:
        """Can the account list spaces? Also finds the `cloud_id` a scoped token needs."""
        base = str(self.config["base_url"]).rstrip("/")
        auth = (str(self.config["email"]), str(self.config.get("api_token", "")))
        cloud_id = self.config.get("cloud_id")
        async with httpx.AsyncClient(timeout=20.0, transport=guarded(TRANSPORT)) as client:
            try:
                if not cloud_id:
                    info = await client.get(f"{base}/_edge/tenant_info")
                    cloud_id = info.json().get("cloudId") if info.status_code == 200 else None
                scoped = self.config.get("auth") == "scoped"
                if scoped and not cloud_id:
                    return {"ok": False, "reason": "cannot find the cloud id of the site: set `cloud_id`"}
                root = f"{GATEWAY}/ex/confluence/{cloud_id}" if scoped else base
                spaces = await client.get(f"{root}/wiki/api/v2/spaces", params={"limit": 250}, auth=auth)
            except httpx.HTTPError as error:
                return {"ok": False, "reason": f"{type(error).__name__}: {error}"[:300]}
        if spaces.status_code >= 400:
            return {"ok": False, "reason": f"{spaces.status_code}: the account or its token is refused"}
        return {
            "ok": True,
            "account_spaces": len(spaces.json().get("results", [])),
            **({"cloud_id": cloud_id} if cloud_id else {}),
        }
