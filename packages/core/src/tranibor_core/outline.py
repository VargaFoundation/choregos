# SPDX-License-Identifier: Apache-2.0
"""The outline of a markdown document: its table of contents, numbered, with where each section is.

A long reference is read the way a person reads a book: the table of contents first, then the
section that answers. A converted document keeps its headings (ADR 0051), so its outline costs
nothing to compute and needs no model. It serves a `files` base's `index.md` (each document's
sections, with the line they start on) and a conversation's `knowledge_get` (a document too long to
return whole answers its outline, and one section is read by its number or its heading path).

Headings are ATX (`#` to `######`); a `#` inside a fenced code block is not one. Sections are
numbered by nesting, not by level: a `###` straight under a `#` is `1.1`, not `1.0.1`. A section
runs to the next heading of its level or above, so it holds its subsections. A YAML header at the
top (an Open Knowledge Format header, ADR 0174) is not text: a `# comment` in it is not a heading,
and the lines and offsets of the sections stay those of the whole document.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
#: A YAML header: `---` on the first line, up to the next `---` or `...` line.
_FRONT_MATTER = re.compile(
    r"\A\ufeff?---[ \t]*\r?\n.*?^(?:---|\.\.\.)[ \t]*\r?$\n?", re.DOTALL | re.MULTILINE
)
#: What separates the headings of a path, as a search hit prints it.
PATH_SEPARATOR = " > "


@dataclass(frozen=True)
class Section:
    #: Its place in the outline: `2`, `2.3`, `2.3.1`.
    number: str
    #: The markdown level of its heading (1 for `#`).
    level: int
    title: str
    #: The titles from the top of the document down to this one.
    path: tuple[str, ...]
    #: The line of its heading, counted from 1.
    line: int
    #: Where it starts (its heading) and ends in the markdown, in characters; it holds its subsections.
    start: int
    end: int

    @property
    def depth(self) -> int:
        return self.number.count(".") + 1

    @property
    def size(self) -> int:
        return self.end - self.start


def _fence_closes(line: str, fence: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and set(stripped) == {fence[0]} and len(stripped) >= len(fence)


def outline(markdown: str) -> list[Section]:
    """The sections of a document, in reading order."""
    found: list[tuple[int, str, int, int]] = []  # level, title, line, offset
    fence: str | None = None
    offset = 0
    header = _FRONT_MATTER.match(markdown)
    skip = header.end() if header else 0
    for line_number, line in enumerate(markdown.splitlines(keepends=True), start=1):
        here, offset = offset, offset + len(line)
        if here < skip:
            continue
        text = line.rstrip("\r\n")
        if fence is not None:
            if _fence_closes(text, fence):
                fence = None
            continue
        if opened := _FENCE.match(text):
            fence = opened.group(1)
            continue
        if heading := _HEADING.match(text):
            found.append((len(heading.group(1)), heading.group(2).strip(), line_number, here))
    sections: list[Section] = []
    stack: list[tuple[int, str, str]] = []  # level, number, title of the open ancestors
    children: dict[str, int] = {}
    for index, (level, title, heading_line, start) in enumerate(found):
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent = stack[-1][1] if stack else ""
        children[parent] = children.get(parent, 0) + 1
        number = f"{parent}.{children[parent]}" if parent else str(children[parent])
        end = next((s for lvl, _, _, s in found[index + 1 :] if lvl <= level), len(markdown))
        path = (*(name for _, _, name in stack), title)
        sections.append(Section(number, level, title, path, heading_line, start, end))
        stack.append((level, number, title))
    return sections


def _normal(text: str) -> str:
    return " ".join(text.split()).casefold()


def find(sections: list[Section], ref: str) -> Section | None:
    """The section a reference names: its number (`2.3`), its heading path (`Encryption > In
    transit`, as a search hit prints it) or, when only one section bears it, its title."""
    wanted = ref.strip().removeprefix("§").strip()
    if not wanted:
        return None
    for section in sections:
        if section.number == wanted.rstrip("."):
            return section
    parts = tuple(_normal(part) for part in wanted.split(PATH_SEPARATOR.strip()) if part.strip())
    for section in sections:
        if tuple(_normal(title) for title in section.path) == parts:
            return section
    # A path that starts above the outline (a file name, the document's title) still ends on a section.
    ending = [s for s in sections if tuple(_normal(t) for t in s.path) == parts[-len(s.path) :]]
    if len(ending) == 1:
        return ending[0]
    titled = [s for s in sections if _normal(s.title) == parts[-1]] if parts else []
    return titled[0] if len(titled) == 1 else None


def _size(characters: int) -> str:
    return f"{characters} chars" if characters < 1000 else f"{characters / 1000:.1f}k chars"


def render(sections: list[Section], *, max_depth: int = 6, max_lines: int = 200, lines: bool = False) -> str:
    """The outline as an indented list: `2.3 In transit — 1.2k chars`, with `(line 42)` when asked.
    Past `max_depth` or `max_lines`, the rest is counted, not listed."""
    shown = [s for s in sections if s.depth <= max_depth]
    out = []
    for section in shown[:max_lines]:
        where = f" (line {section.line})" if lines else ""
        indent = "  " * (section.depth - 1)
        out.append(f"{indent}- {section.number} {section.title} — {_size(section.size)}{where}")
    hidden = len(sections) - min(len(shown), max_lines)
    if hidden > 0:
        out.append(f"- … {hidden} more section(s) not listed")
    return "\n".join(out)


__all__ = ["PATH_SEPARATOR", "Section", "find", "outline", "render"]
