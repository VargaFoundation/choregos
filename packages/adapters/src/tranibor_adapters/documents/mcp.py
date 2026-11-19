# SPDX-License-Identifier: Apache-2.0
"""The resources of an MCP server as a knowledge source (ADR 0191).

Any MCP server the organisation declared may feed a knowledge base: `resources/list` says what it
holds, `resources/read` gives each one. Nothing here knows which product the server belongs to (a
data platform, a wiki, a catalogue): it reads the protocol, and the protocol only.

- **Identity**: a resource is its `uri`. The listing is complete (`full`): a resource the server no
  longer lists leaves the base at the next sync. A listing that fails fails the sync, so a server
  down never empties a base.
- **Version**: the `annotations.lastModified` the server declares, when it declares one. Without it
  every sync reads every resource again, and the platform's digest says whether it changed: correct,
  and as costly as the server's resources are large.
- **Name**: the resource's `name` (or the last segment of its URI), with the extension its
  `mimeType` calls for, so that the platform converts it as what it is. Two resources listed under
  the same name both carry a short digest of their URI, so neither replaces the other.
- **Content**: a `text` is UTF-8, a `blob` is base64. A resource that answers several contents (a
  collection) is one document: its JSON parts as one array, its text parts one after the other. A
  resource that answers nothing readable, or that is gone since the listing, is skipped and said; the
  sync goes on.

A document's own Open Knowledge Format header (ADR 0174) is read by the platform as for any source:
it is how the server says a resource is `deprecated`, what it is titled, where it comes from.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import uuid
from pathlib import PurePosixPath
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

from .base import Changes, ItemUnreadableError, SourceItem, SourceRefusedError

#: The extension a known media type is converted as.
EXTENSIONS: dict[str, str] = {
    "text/markdown": ".md",
    "text/x-markdown": ".md",
    "text/plain": ".txt",
    "text/html": ".html",
    "application/xhtml+xml": ".html",
    "application/json": ".json",
    "application/yaml": ".yaml",
    "application/x-yaml": ".yaml",
    "text/yaml": ".yaml",
    "application/toml": ".toml",
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}
#: Extensions that mean the same format: a `notes.markdown` stays as it is.
_SAME = {".markdown": ".md", ".htm": ".html", ".yml": ".yaml"}
#: At most this many characters of a name, extension included.
NAME_MAX = 200
#: At most this many characters of `uri_prefix`, and this many media types in `mime_types`.
PREFIX_MAX = 2000
MIME_TYPES_MAX = 20
#: At most this many contents of one resource are joined into its document.
MAX_PARTS = 1000

_UNSAFE = re.compile(r"[\x00-\x1f\x7f/\\]+")


class ResourceClient(Protocol):
    """What the source needs of an MCP client: `McpClient` and a gateway's client both have it."""

    async def list_resources(self) -> list[Any]: ...

    async def read_resource(self, uri: str) -> list[dict[str, Any]]: ...


def checked_config(config: dict[str, Any]) -> dict[str, Any]:
    """The configuration a source keeps: optional `uri_prefix` and `mime_types`, nothing else."""
    out: dict[str, Any] = {}
    prefix = config.get("uri_prefix")
    if prefix not in (None, ""):
        if not isinstance(prefix, str) or len(prefix) > PREFIX_MAX:
            raise SourceRefusedError(f"`uri_prefix` is a text of at most {PREFIX_MAX} characters")
        out["uri_prefix"] = prefix
    types = config.get("mime_types")
    if types not in (None, []):
        if (
            not isinstance(types, list)
            or len(types) > MIME_TYPES_MAX
            or not all(isinstance(t, str) and re.fullmatch(r"[\w.+-]+/[\w.+-]+", t) for t in types)
        ):
            raise SourceRefusedError(
                f"`mime_types` is a list of at most {MIME_TYPES_MAX} media types (`text/markdown`)"
            )
        out["mime_types"] = sorted({t.lower() for t in types})
    return out


def file_name(name: str, uri: str, mime_type: str | None) -> str:
    """The file name a resource is stored under: its extension says how it is converted."""
    raw = name.strip() or PurePosixPath(unquote(urlsplit(uri).path)).name or "resource"
    clean = _UNSAFE.sub("-", raw).strip(" .-") or "resource"
    suffix = PurePosixPath(clean.lower()).suffix
    wanted = EXTENSIONS.get((mime_type or "").split(";")[0].strip().lower())
    if wanted is None and (mime_type or "").lower().startswith("text/") and not suffix:
        wanted = ".txt"
    if wanted is not None and _SAME.get(suffix, suffix) != wanted:
        clean += wanted
    if len(clean) > NAME_MAX:
        stem, ext = clean[: -len(PurePosixPath(clean).suffix) or None], PurePosixPath(clean).suffix
        clean = stem[: NAME_MAX - len(ext)] + ext
    return clean


def _tagged(name: str, uri: str) -> str:
    """`name` with a short digest of `uri` before its extension: two resources, two files."""
    tag = hashlib.sha256(uri.encode()).hexdigest()[:8]
    path = PurePosixPath(name)
    stem = path.name[: -len(path.suffix)] if path.suffix else path.name
    return f"{stem[: NAME_MAX - len(path.suffix) - 9]}-{tag}{path.suffix}"


class McpResourceSource:
    """A knowledge source over the resources of one MCP server."""

    def __init__(
        self,
        client: ResourceClient,
        *,
        uri_prefix: str | None = None,
        mime_types: list[str] | None = None,
    ) -> None:
        self._client = client
        self._prefix = uri_prefix or ""
        self._types = {t.lower() for t in mime_types or []}

    def _wanted(self, resource: Any) -> bool:
        if not str(resource.uri).startswith(self._prefix):
            return False
        if self._types:
            return (resource.mime_type or "").split(";")[0].strip().lower() in self._types
        return True

    async def changes(self, cursor: dict[str, Any] | None) -> Changes:
        listed = sorted(
            (r for r in await self._client.list_resources() if self._wanted(r)), key=lambda r: r.uri
        )
        names = {r.uri: file_name(r.name, r.uri, r.mime_type) for r in listed}
        seen: dict[str, int] = {}
        for name in names.values():
            seen[name.lower()] = seen.get(name.lower(), 0) + 1
        # One nonce per listing: a resource with no `lastModified` is read again at every sync.
        unversioned = f"unversioned:{uuid.uuid4().hex}"
        items = [
            SourceItem(
                ref=r.uri,
                name=_tagged(names[r.uri], r.uri) if seen[names[r.uri].lower()] > 1 else names[r.uri],
                version=f"modified:{r.last_modified}" if r.last_modified else unversioned,
                url=r.uri if urlsplit(r.uri).scheme in {"http", "https"} else None,
                size=r.size,
            )
            for r in listed
        ]
        return Changes(items=items, cursor=None, full=True)

    async def fetch(self, item: SourceItem) -> bytes:
        from ..mcp import McpError

        try:
            contents = await self._client.read_resource(item.ref)
        except McpError as error:
            if error.code in {-32002, 404}:
                raise ItemUnreadableError(f"gone since the listing: {error}") from error
            raise
        own = [c for c in contents if c.get("uri") == item.ref] or contents
        if not own:
            raise ItemUnreadableError("no content answered")
        if len(own) > 1:
            return _collection(item, own)
        content = own[0]
        if isinstance(content.get("text"), str):
            return str(content["text"]).encode("utf-8")
        if isinstance(content.get("blob"), str):
            try:
                return base64.b64decode(content["blob"], validate=True)
            except (binascii.Error, ValueError) as error:
                raise ItemUnreadableError("its `blob` is not base64") from error
        raise ItemUnreadableError("neither a `text` nor a `blob`")


def _collection(item: SourceItem, contents: list[dict[str, Any]]) -> bytes:
    """A resource that answers several contents (a collection, each part under its own URI) is one
    document: JSON parts as one JSON array, text parts one after the other. Binary parts cannot be
    joined: the resource is skipped and said."""
    if len(contents) > MAX_PARTS:
        raise ItemUnreadableError(f"{len(contents)} contents for one resource, at most {MAX_PARTS}")
    texts = [c.get("text") for c in contents]
    if not all(isinstance(t, str) for t in texts):
        raise ItemUnreadableError(f"{len(contents)} contents, not all of them text: they cannot be joined")
    parts = [str(t) for t in texts]
    if PurePosixPath(item.name.lower()).suffix == ".json":
        try:
            joined = json.dumps([json.loads(t) for t in parts], ensure_ascii=False, indent=2)
        except ValueError as error:
            raise ItemUnreadableError(f"{len(parts)} contents, not all of them JSON") from error
        return joined.encode("utf-8")
    return "\n\n".join(t.rstrip("\n") for t in parts).encode("utf-8") + b"\n"
