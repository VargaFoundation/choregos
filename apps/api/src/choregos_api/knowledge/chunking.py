# SPDX-License-Identifier: Apache-2.0
"""Contextual chunks of a converted document (ADR 0051, serving mode `index`).

A chunk alone ("TLS 1.3 everywhere.") does not say what it is about; prefixed by its document's
title and the path of headings above it ("Encryption standard > In transit"), it does — for the
full-text rank and for the embedding alike. The prefix is indexed; the citation shows the heading
path and the chunk's own text.

Sections are cut at headings; a long section at paragraph boundaries, a table kept whole when it
fits. Slide markers of a converted deck (`<!-- Slide number: 3 -->`) are dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_COMMENT = re.compile(r"^\s*<!--.*?-->\s*$")
#: Characters of a chunk's own text: about 300 tokens, a paragraph or a small table.
MAX_CHARS = 1200


@dataclass
class Chunk:
    ordinal: int
    heading_path: list[str]
    body: str
    text: str = field(default="")


def _paragraphs(lines: list[str]) -> list[str]:
    blocks, current = [], []  # type: ignore[var-annotated]
    for line in lines:
        if not line.strip():
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _pack(blocks: list[str], limit: int) -> list[str]:
    """Paragraphs gathered up to `limit` characters; a paragraph longer than that is cut by lines."""
    packed: list[str] = []
    current = ""
    for block in blocks:
        pieces = [block] if len(block) <= limit else _split_long(block, limit)
        for piece in pieces:
            if current and len(current) + 2 + len(piece) > limit:
                packed.append(current)
                current = piece
            else:
                current = f"{current}\n\n{piece}" if current else piece
    if current:
        packed.append(current)
    return packed


def _split_long(block: str, limit: int) -> list[str]:
    out, current = [], ""
    for whole in block.split("\n"):
        line = whole
        while len(line) > limit:
            out.append(line[:limit])
            line = line[limit:]
        if current and len(current) + 1 + len(line) > limit:
            out.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        out.append(current)
    return out


def chunk(markdown: str, title: str, *, limit: int = MAX_CHARS) -> list[Chunk]:
    """The contextual chunks of one document, in reading order."""
    sections: list[tuple[list[str], list[str]]] = []
    path: list[tuple[int, str]] = []
    lines: list[str] = []

    def close() -> None:
        if any(line.strip() for line in lines):
            sections.append(([name for _, name in path], list(lines)))
        lines.clear()

    for line in markdown.splitlines():
        if _COMMENT.match(line):
            continue
        heading = _HEADING.match(line)
        if heading:
            close()
            level = len(heading.group(1))
            path = [(lvl, name) for lvl, name in path if lvl < level]
            path.append((level, heading.group(2).strip()))
            continue
        lines.append(line)
    close()

    chunks: list[Chunk] = []
    for headings, body_lines in sections:
        for body in _pack(_paragraphs(body_lines), limit):
            context = " > ".join([title, *[h for h in headings if h != title]])
            chunks.append(
                Chunk(ordinal=len(chunks), heading_path=headings, body=body, text=f"{context}\n\n{body}")
            )
    return chunks
