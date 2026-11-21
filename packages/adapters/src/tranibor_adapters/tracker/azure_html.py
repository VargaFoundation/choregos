# SPDX-License-Identifier: Apache-2.0
"""Markdown <-> the HTML Azure Boards keeps in descriptions and comments, just what Tranibor needs.

A work item's description and its comments are HTML (the Discussion control of the 7.1 API has no
other stored form). The status comment (§4.3) is a heading, paragraphs, a list and a table: that
subset is translated, the rest goes out escaped in a paragraph. Everything third-party is escaped
before it is wrapped in a tag, so a title or a cell can never inject markup.
"""

from __future__ import annotations

import html
import re

from .adf import BOLD, BULLET, CODE, HEADING, LINK, TABLE_SEPARATOR

_TAG = re.compile(r"<[^>]+>")
_BREAK = re.compile(r"<\s*(?:br\s*/?|/p|/div|/li|/tr|/h[1-6])\s*>", re.IGNORECASE)
_BLANKS = re.compile(r"\n{3,}")


def markdown_to_html(markdown: str) -> str:
    lines = (markdown or "").splitlines()
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        heading = HEADING.match(line)
        if heading:
            level = min(len(heading.group(1)), 6)
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            index += 1
        elif _is_row(line) and index + 1 < len(lines) and TABLE_SEPARATOR.match(lines[index + 1]):
            table, index = _table(lines, index)
            out.append(table)
        elif BULLET.match(line):
            items: list[str] = []
            while index < len(lines) and (bullet := BULLET.match(lines[index])):
                items.append(f"<li>{_inline(bullet.group(1))}</li>")
                index += 1
            out.append(f"<ul>{''.join(items)}</ul>")
        else:
            paragraph: list[str] = []
            while index < len(lines) and lines[index].strip() and not HEADING.match(lines[index]):
                if paragraph and (BULLET.match(lines[index]) or _is_row(lines[index])):
                    break
                paragraph.append(lines[index].strip())
                index += 1
            out.append(f"<p>{'<br>'.join(_inline(p) for p in paragraph)}</p>")
    return "".join(out)


def html_to_text(value: object) -> str:
    """The text of a description or a comment: tags dropped, breaks kept as newlines, entities decoded."""
    if not isinstance(value, str) or not value:
        return ""
    text = _TAG.sub("", _BREAK.sub("\n", value))
    return _BLANKS.sub("\n\n", html.unescape(text)).strip()


def _is_row(line: str) -> bool:
    return line.strip().startswith("|") and line.strip().endswith("|")


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _table(lines: list[str], index: int) -> tuple[str, int]:
    head = "".join(f"<th>{_inline(c)}</th>" for c in _cells(lines[index]))
    index += 2
    rows: list[str] = []
    while index < len(lines) and _is_row(lines[index]):
        rows.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in _cells(lines[index])) + "</tr>")
        index += 1
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>", index


def _inline(text: str) -> str:
    escaped = html.escape(text, quote=True)
    escaped = CODE.sub(lambda m: f"<code>{m.group(1)}</code>", escaped)
    escaped = BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", escaped)
    return LINK.sub(lambda m: _link(m.group(1), m.group(2)), escaped)


def _link(label: str, target: str) -> str:
    """Only http(s) targets become links; `javascript:` and the rest stay text."""
    url = html.unescape(target)
    if not url.lower().startswith(("http://", "https://")):
        return f"{label} ({target})"
    return f'<a href="{target}">{label}</a>'
