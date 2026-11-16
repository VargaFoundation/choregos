# SPDX-License-Identifier: Apache-2.0
"""The Open Knowledge Format at the edge of a knowledge base (ADR 0174, OKF v0.2).

OKF is a directory of markdown files, each opening with a YAML header whose only required key is
`type`. Choregos neither stores nor trusts it: the database stays the store, rights stay ADR 0081's.
It is read where a document comes in and written where a document goes out to an agent:

- `split()` reads a document's header when it has one (a `.md` file from git, SharePoint or an
  upload); a header that is not a YAML mapping is not a header, and stays text.
- `deprecated()` says whether a document declares itself withdrawn: it is neither served nor indexed.
- `served()` is the markdown an agent reads: the document's own header, kept key for key, completed
  with what the platform knows (`type`, `title`, `resource`, `generated`, `sources`).

A `verified` key that came with a document is a claim of its author, not a verification the platform
made: an agent or a consumer would derive a trust tier from it (OKF §6). It is served renamed
`source_verified`, so nothing is lost and nobody reads it as the platform's word.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import yaml

OKF_VERSION = "0.2"
#: The actor that converts a document (OKF §5: `process:<id>` for an automated process).
PRODUCER = "process:choregos-knowledge"
#: Names OKF reserves in every directory of a bundle; no document is laid down under them.
RESERVED = frozenset({"index.md", "log.md"})
#: The type of a document whose header names none.
DEFAULT_TYPE = "Document"
#: A header larger than this is not read as one (a document is up to 20 MiB; a header is a few lines).
MAX_HEADER_BYTES = 64 * 1024

_HEADER = re.compile(r"\A﻿?---[ \t]*\r?\n(?P<yaml>.*?)^---[ \t]*\r?$\n?", re.DOTALL | re.MULTILINE)
_TIMESTAMP = "tag:yaml.org,2002:timestamp"


class _Loader(yaml.SafeLoader):
    """A safe loader that leaves timestamps as the text they were written in: a header served back
    must say `2026-07-24T10:00:00Z`, not the representation of a parsed `datetime`."""


_Loader.yaml_implicit_resolvers = {
    first: [(tag, pattern) for tag, pattern in resolvers if tag != _TIMESTAMP]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def split(markdown: str) -> tuple[dict[str, Any] | None, str]:
    """The document's OKF header and its body; `(None, markdown)` when it opens with none."""
    match = _HEADER.match(markdown)
    if match is None or len(match.group("yaml").encode("utf-8")) > MAX_HEADER_BYTES:
        return None, markdown
    try:
        header = yaml.load(match.group("yaml"), Loader=_Loader)  # noqa: S506 - a safe loader
    except yaml.YAMLError:
        return None, markdown
    if header is None:
        header = {}
    if not isinstance(header, dict):
        return None, markdown
    return {str(key): value for key, value in header.items()}, markdown[match.end() :].lstrip("\n")


def declared_title(markdown: str) -> str | None:
    """The `title` a document's header declares, if any."""
    header, _ = split(markdown)
    title = (header or {}).get("title")
    return title.strip() if isinstance(title, str) and title.strip() else None


def deprecated(markdown: str) -> bool:
    """Whether the document declares itself withdrawn (OKF `status: deprecated`)."""
    header, _ = split(markdown)
    status = (header or {}).get("status")
    return isinstance(status, str) and status.strip().lower() == "deprecated"


def _instant(moment: datetime) -> str:
    """ISO 8601 with an explicit offset (OKF §5); a naive value from the database is UTC."""
    moment = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def served(
    markdown: str,
    *,
    title: str,
    filename: str,
    source_url: str | None = None,
    converted_at: datetime | None = None,
) -> str:
    """The markdown an agent is served: one OKF header, then the body. Deterministic for a given
    document (the run's digest is computed over it twice, when prepared and when laid down)."""
    imported, body = split(markdown)
    own = dict(imported or {})
    claimed = own.pop("verified", None)
    kind = own.pop("type", None)
    header: dict[str, Any] = {
        "type": kind if isinstance(kind, str) and kind.strip() else DEFAULT_TYPE,
        "title": own.pop("title", None) or title,
    }
    if source_url:
        header["resource"] = source_url
    if converted_at is not None:
        header["generated"] = {"by": PRODUCER, "at": _instant(converted_at)}
    header["sources"] = [{"resource": source_url or filename, "title": filename}]
    # What the document said of itself wins over what the platform infers, key for key.
    header.update(own)
    if claimed is not None:
        header["source_verified"] = claimed
    text = yaml.safe_dump(header, sort_keys=False, allow_unicode=True, default_flow_style=False, width=4096)
    return f"---\n{text}---\n\n{body}"


def served_document(document: Any) -> str:
    """`served()` of a `KnowledgeDocument`."""
    return served(
        document.markdown or "",
        title=document.title,
        filename=document.filename,
        source_url=document.source_url,
        converted_at=document.converted_at,
    )


__all__ = [
    "DEFAULT_TYPE",
    "OKF_VERSION",
    "PRODUCER",
    "RESERVED",
    "declared_title",
    "deprecated",
    "served",
    "served_document",
    "split",
]
