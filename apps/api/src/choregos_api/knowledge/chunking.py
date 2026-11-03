# SPDX-License-Identifier: Apache-2.0
"""Contextual chunks of a converted document (ADR 0051, serving mode `index`).

A chunk alone ("TLS 1.3 everywhere.") does not say what it is about; prefixed by its document's
title and the path of headings above it ("Encryption standard > In transit"), it does — for the
full-text rank and for the embedding alike. The prefix is indexed; the citation shows the heading
path and the chunk's own text.

Sections are cut at headings; a long section at paragraph boundaries, a table kept whole when it
fits. Slide markers of a converted deck (`<!-- Slide number: 3 -->`) are dropped.

A fenced code block (a YAML or JSON file is stored as one, S51-05) is one paragraph: a `# comment`
inside it is not a heading, a blank line inside it does not cut it, and a block longer than a chunk is
cut by lines, each piece fenced again so that every chunk still reads as code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_COMMENT = re.compile(r"^\s*<!--.*?-->\s*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
#: Characters of a chunk's own text: about 300 tokens, a paragraph or a small table.
MAX_CHARS = 1200


@dataclass
class Chunk:
    ordinal: int
    heading_path: list[str]
    body: str
    text: str = field(default="")


def _fence_opened(line: str) -> str | None:
    """The fence a line opens (its run of backticks or tildes), if it opens one."""
    found = _FENCE.match(line)
    return found.group(1) if found else None


def _fence_closes(line: str, fence: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and set(stripped) == {fence[0]} and len(stripped) >= len(fence)


def _paragraphs(lines: list[str]) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    fence: str | None = None
    for line in lines:
        if fence is not None:
            current.append(line)
            if _fence_closes(line, fence):
                fence = None
                blocks.append("\n".join(current))
                current = []
            continue
        opened = _fence_opened(line)
        if opened is not None:
            if current:
                blocks.append("\n".join(current))
            fence, current = opened, [line]
            continue
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
    lines = block.split("\n")
    fence = _fence_opened(lines[0])
    if fence is not None and len(lines) > 2 and _fence_closes(lines[-1], fence):
        opener, closer = lines[0], lines[-1]
        room = max(limit - len(opener) - len(closer) - 2, 1)
        pieces = (piece.strip("\n") for piece in _split_lines(lines[1:-1], room))
        return [f"{opener}\n{piece}\n{closer}" for piece in pieces if piece.strip()]
    return _split_lines(lines, limit)


def _split_lines(lines: list[str], limit: int) -> list[str]:
    out, current = [], ""
    for whole in lines:
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

    fence: str | None = None
    for line in markdown.splitlines():
        if fence is not None:
            lines.append(line)
            if _fence_closes(line, fence):
                fence = None
            continue
        if (opened := _fence_opened(line)) is not None:
            fence = opened
            lines.append(line)
            continue
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
