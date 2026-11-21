# SPDX-License-Identifier: Apache-2.0
"""Markdown to Jira wiki markup, just what Tranibor needs, for Jira Data Center.

Jira Data Center and Server answer the REST API v2, whose text fields are wiki markup (or plain
text), not Atlassian Document Format. The same subset as `adf.py` is translated (headings, bold,
code, links, bullet lists, tables); the rest goes through as plain text.
"""

from __future__ import annotations

import re

from .adf import BOLD, BULLET, CODE, HEADING, LINK, TABLE_SEPARATOR, UNESCAPED_PIPE

_ESCAPE = re.compile(r"([{}\[\]])")


def _inline(text: str) -> str:
    """Bold, code and links; the characters wiki markup would read as macros or links are escaped."""
    out: list[str] = []
    position = 0
    pattern = re.compile(f"{BOLD.pattern}|{CODE.pattern}|{LINK.pattern}")
    for match in pattern.finditer(text):
        out.append(_ESCAPE.sub(r"\\\1", text[position : match.start()]))
        bold, code, label, href = match.group(1), match.group(2), match.group(3), match.group(4)
        if bold is not None:
            out.append(f"*{bold}*")
        elif code is not None:
            out.append("{{" + code + "}}")
        else:
            out.append(f"[{label}|{href}]")
        position = match.end()
    out.append(_ESCAPE.sub(r"\\\1", text[position:]))
    return "".join(out)


def _is_row(line: str) -> bool:
    return line.strip().startswith("|") and line.strip().endswith("|")


def markdown_to_wiki(markdown: str) -> str:
    """Translates simple markdown into Jira wiki markup."""
    lines = (markdown or "").splitlines()
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            out.append("")
            index += 1
            continue
        heading = HEADING.match(line)
        if heading:
            out.append(f"h{min(len(heading.group(1)), 6)}. {_inline(heading.group(2))}")
        elif _is_row(line) and index + 1 < len(lines) and TABLE_SEPARATOR.match(lines[index + 1]):
            cells = [c.strip().replace("\\|", "|") for c in UNESCAPED_PIPE.split(line.strip().strip("|"))]
            out.append("||" + "||".join(_inline(c) for c in cells) + "||")
            index += 2
            while index < len(lines) and _is_row(lines[index]):
                row = UNESCAPED_PIPE.split(lines[index].strip().strip("|"))
                out.append("|" + "|".join(_inline(c.strip().replace("\\|", "|")) or " " for c in row) + "|")
                index += 1
            continue
        elif BULLET.match(line):
            out.append(f"* {_inline(BULLET.match(line).group(1))}")  # type: ignore[union-attr]
        else:
            out.append(_inline(line))
        index += 1
    return "\n".join(out).strip()


_UNESCAPE = re.compile(r"\\([{}\[\]])")


def text_of_wiki(value: object) -> str:
    """What Tranibor reads back from a v2 text field: the string, with the escapes `markdown_to_wiki`
    added removed, so that a marker such as `[choregos:status]` is found again."""
    return _UNESCAPE.sub(r"\1", value) if isinstance(value, str) else ""
