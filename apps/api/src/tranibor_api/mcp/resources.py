# SPDX-License-Identifier: Apache-2.0
"""The door's documentation as MCP resources (ADR 0191 §8).

The pages `docs_search` and `docs_read` serve to a model are also listed by `resources/list` and
given by `resources/read`, so that any MCP client that reads resources (a knowledge base of this
platform or of another one) can copy the documentation of the version running here.

- A resource is a page: its URI is the page's public URL, its `name` the page's slug, its
  `lastModified` the file's, so that a reader reads again only what changed.
- Its text is the page's Markdown under an Open Knowledge Format header that carries its title.
- Listed by pages of `PAGE_SIZE`, the cursor being where the next page starts.
- No bundled documentation: no resource, and a read answers `-32002` as for an unknown page.
"""

from __future__ import annotations

from typing import Any

import yaml
from tranibor_core.mcp import INVALID_PARAMS, RESOURCE_NOT_FOUND

from ..config import get_settings
from . import docs

#: At most this many resources per `resources/list` answer.
PAGE_SIZE = 100
MIME_TYPE = "text/markdown"


class ResourceRefusedError(Exception):
    """A request the door answers with a JSON-RPC error: its code and its message."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def _index() -> tuple[docs.Index | None, str]:
    settings = get_settings()
    return docs.index_of(settings.docs_dir), settings.docs_url


def text_of(page: docs.Page) -> str:
    """What a reader is given: the page under a header saying its title (ADR 0174)."""
    header = yaml.safe_dump({"title": page.title}, allow_unicode=True, width=4096)
    return f"---\n{header}---\n\n{page.markdown}"


def _resource(page: docs.Page, base: str) -> dict[str, Any]:
    return {
        "uri": page.url(base),
        "name": page.slug or "index",
        "title": page.title,
        "mimeType": MIME_TYPE,
        "size": len(text_of(page).encode("utf-8")),
        "annotations": {"lastModified": page.modified},
    }


def listing(params: dict[str, Any]) -> dict[str, Any]:
    """One page of `resources/list`: the documentation's pages, by slug."""
    index, base = _index()
    pages = list(index.pages.values()) if index is not None else []
    cursor = params.get("cursor")
    start = 0
    if cursor is not None:
        if not isinstance(cursor, str) or not cursor.isdigit() or int(cursor) > len(pages):
            raise ResourceRefusedError(INVALID_PARAMS, "unknown cursor: start again without one")
        start = int(cursor)
    end = start + PAGE_SIZE
    result: dict[str, Any] = {"resources": [_resource(p, base) for p in pages[start:end]]}
    if end < len(pages):
        result["nextCursor"] = str(end)
    return result


def reading(params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """`resources/read` of one page: its slug (for the audit) and the result."""
    uri = params.get("uri")
    if not isinstance(uri, str) or not uri.strip():
        raise ResourceRefusedError(INVALID_PARAMS, "argument `uri` missing: one that resources/list gave")
    index, base = _index()
    if index is None:
        raise ResourceRefusedError(RESOURCE_NOT_FOUND, f"Resource not found: {uri}")
    page = index.pages.get(docs.normalize_slug(uri, base))
    if page is None or page.url(base) != uri:
        raise ResourceRefusedError(RESOURCE_NOT_FOUND, f"Resource not found: {uri}")
    return page.slug or "index", {"contents": [{"uri": uri, "mimeType": MIME_TYPE, "text": text_of(page)}]}
