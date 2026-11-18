# SPDX-License-Identifier: Apache-2.0
"""A PDF's bookmarks become the headings of its markdown (ADR 0051, update of 2026-11-16).

MarkItDown reads a PDF's text without its headings, so a converted PDF had no outline: its chunks had
no heading path and `knowledge_get` could not read it by section. Most PDFs exported from a word
processor or LaTeX carry their table of contents as bookmarks (the document outline), which pdfminer
(already there, under MarkItDown) reads without rendering anything. Each bookmark is looked for in the
text, in order, and the line that carries it becomes a heading of the bookmark's depth.

A bookmark is found on a line that says its title, ignoring case, spacing and punctuation, possibly
after a section number (`1.2 Scope` for the bookmark `Scope`), or on two lines the extractor broke it
over. The search only moves forward, so headings stay in the order of the outline, and a line made a
heading is not one a later bookmark can take. A bookmark not found gets no heading: a heading missed
costs a section boundary, a heading invented would cost a wrong one. It is not a conversion warning
either: those say what was cut from the text (a sheet past its row cap), and nothing is. A PDF without
bookmarks (a scan, a print to PDF) is unchanged.
"""

from __future__ import annotations

import bisect
import io
import re

_NOT_WORD = re.compile(r"[\W_]+")
#: A section number before a heading's title: `1.2`, `1.2.`, `IV.`, `2)`, `A.`.
_NUMBERED = re.compile(r"^\s*(?:[0-9]+|[IVXLC]+|[A-Z])(?:\.[0-9]+)*[.)]?\s+(?=\S)")
#: Past this many bookmarks the outline is an index of figures or pages, not a table of contents.
MAX_BOOKMARKS = 2000


def _normal(text: str) -> str:
    return _NOT_WORD.sub("", text.casefold())


def bookmarks(data: bytes) -> list[tuple[int, str]]:
    """The PDF's outline as (depth from 1, title), in order; empty when it has none or cannot be read."""
    from pdfminer.pdfdocument import PDFDocument, PDFNoOutlines
    from pdfminer.pdfparser import PDFParser
    from pdfminer.utils import decode_text

    try:
        document = PDFDocument(PDFParser(io.BytesIO(data)))
        found: list[tuple[int, str]] = []
        for level, title, *_ in document.get_outlines():
            text = decode_text(title) if isinstance(title, bytes) else str(title or "")
            if text.strip():
                found.append((max(1, int(level)), " ".join(text.split())))
            if len(found) > MAX_BOOKMARKS:
                return []
        return found
    except PDFNoOutlines:
        return []
    except Exception:  # a damaged outline: the text alone is still worth having
        return []


def _keys(text: str) -> set[str]:
    """What a line (or two lines joined) may be found by: its words, with or without its section number."""
    keys = {_normal(text), _normal(_NUMBERED.sub("", text, count=1))}
    keys.discard("")
    return keys


def with_headings(markdown: str, outline: list[tuple[int, str]]) -> tuple[str, int]:
    """The markdown with a heading on each line a bookmark names, and how many bookmarks it placed.

    Each line, and each pair of lines, is indexed once by what it says, so placing the outline is linear
    in the text: a long PDF whose bookmarks are not in its text does not exhaust the conversion's time."""
    if not outline:
        return markdown, 0
    lines = markdown.split("\n")
    where: dict[str, list[tuple[int, int]]] = {}  # what it says -> (first line, lines it spans), in order
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        for key in _keys(line):
            where.setdefault(key, []).append((index, 1))
        following = lines[index + 1] if index + 1 < len(lines) else ""
        if following.strip():
            for key in _keys(f"{line} {following}") - _keys(line):
                where.setdefault(key, []).append((index, 2))
    cursor, placed = 0, 0
    for level, title in outline:
        found = where.get(_normal(title), [])
        at = bisect.bisect_left(found, (cursor, 0))
        if at == len(found):
            continue
        index, span = found[at]
        said = " ".join(" ".join(lines[index : index + span]).split())
        lines[index : index + span] = [f"{'#' * min(level, 6)} {said}", *([""] * (span - 1))]
        cursor, placed = index + span, placed + 1
    # A heading needs a blank line before it to be one in markdown.
    text = "\n".join(lines)
    text = re.sub(r"(?m)([^\n])\n(#{1,6} )", r"\1\n\n\2", text)
    text = re.sub(r"(?m)^(#{1,6} [^\n]*)\n(?=[^\n#])", r"\1\n\n", text)
    return text, placed


def headed(markdown: str, data: bytes) -> str:
    """The markdown of a PDF with its bookmarks as headings."""
    return with_headings(markdown, bookmarks(data))[0]


__all__ = ["bookmarks", "headed", "with_headings"]
