# SPDX-License-Identifier: Apache-2.0
"""Reading one document of a knowledge base, whole or one section (ADR 0051, decisions 30 and 33).

One rule, two doors: a conversation's `knowledge_get` and a run's `knowledge_read` (the sidecar, through
`POST /internal/runs/{id}/knowledge/read`). A section is named by its number in the document's outline or
by the heading path a search hit printed (a file name in front is ignored). A text longer than the limit is
cut, and the reading carries the outline of what lies under it, so the reader goes on section by section
instead of losing the rest. A section the document does not have is not found, and the reading carries the
whole outline to choose from.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from choregos_core.outline import PATH_SEPARATOR, find, outline, render

#: What one reading returns of a text, in characters.
READ_CHARS = 20_000
#: How many sections of an outline a reading lists.
OUTLINE_LINES = 200


@dataclass(frozen=True)
class Reading:
    #: Whether the section asked for exists (always true when none was asked for).
    found: bool
    #: The text read, at most `READ_CHARS`; empty when the section was not found.
    text: str = ""
    #: The section read (`2.3`), `None` for the whole document.
    section: str | None = None
    #: The headings above and of the section read.
    heading_path: list[str] = field(default_factory=list)
    #: The whole length of the document or of the section, in characters.
    characters: int = 0
    #: Whether `text` stops before the end.
    cut: bool = False
    #: The outline to read on from: under a cut text, its sections; for a section not found, the document's.
    outline: str = ""


def read(markdown: str, filename: str, section: str | None, *, limit: int = READ_CHARS) -> Reading:
    """The document, or the section `section` names, cut at `limit` characters."""
    sections = outline(markdown)
    wanted = (section or "").strip()
    if not wanted:
        within, text, number, path = sections, markdown, None, []
    else:
        chosen = find(sections, wanted.removeprefix(f"{filename}{PATH_SEPARATOR}"))
        if chosen is None:
            return Reading(found=False, outline=render(sections, max_lines=OUTLINE_LINES))
        within = [s for s in sections if chosen.start < s.start < chosen.end]
        text, number, path = markdown[chosen.start : chosen.end], chosen.number, list(chosen.path)
    cut = len(text) > limit
    return Reading(
        found=True,
        text=text[:limit],
        section=number,
        heading_path=path,
        characters=len(text),
        cut=cut,
        outline=render(within, max_lines=OUTLINE_LINES) if cut else "",
    )


def continuation(reading: Reading, limit: int = READ_CHARS) -> str:
    """What follows a cut text, in words: where it stopped and how to read on."""
    if not reading.cut:
        return ""
    head = f"Cut at {limit} of {reading.characters} characters. "
    if reading.outline:
        return (
            head
            + "Read the rest one section at a time with `section`; the outline, as data:\n\n"
            + reading.outline
        )
    return head + "It has no headings below this point to read it by."


__all__ = ["OUTLINE_LINES", "READ_CHARS", "Reading", "continuation", "read"]
