"""Markdown ↔ Atlassian Document Format, juste ce qu'il faut pour Choregos.

Jira Cloud n'accepte plus de markdown dans ses commentaires : il veut de l'ADF. Le
commentaire de suivi (§4.3) est un titre, quelques paragraphes, une liste et un tableau —
c'est ce sous-ensemble qui est traduit, et rien de plus. Ce qui n'est pas reconnu part en
paragraphe littéral : un commentaire un peu moins joli vaut mieux qu'un commentaire perdu.
"""

from __future__ import annotations

import re
from typing import Any

BOLD = re.compile(r"\*\*(.+?)\*\*")
CODE = re.compile(r"`([^`]+)`")
LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
BULLET = re.compile(r"^\s*[-*]\s+(.*)$")
TABLE_SEPARATOR = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")


def markdown_to_adf(markdown: str) -> dict[str, Any]:
    """Traduit un markdown simple en document ADF."""
    lines = (markdown or "").splitlines()
    content: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        heading = HEADING.match(line)
        if heading:
            content.append(
                {
                    "type": "heading",
                    "attrs": {"level": min(len(heading.group(1)), 6)},
                    "content": _inline(heading.group(2)),
                }
            )
            index += 1
            continue
        if _is_table_row(line) and index + 1 < len(lines) and TABLE_SEPARATOR.match(lines[index + 1]):
            table, index = _table(lines, index)
            content.append(table)
            continue
        if BULLET.match(line):
            items: list[dict[str, Any]] = []
            while index < len(lines) and BULLET.match(lines[index]):
                text = BULLET.match(lines[index]).group(1)  # type: ignore[union-attr]
                items.append(
                    {"type": "listItem", "content": [{"type": "paragraph", "content": _inline(text)}]}
                )
                index += 1
            content.append({"type": "bulletList", "content": items})
            continue
        paragraph: list[str] = []
        while index < len(lines) and lines[index].strip() and not HEADING.match(lines[index]):
            if BULLET.match(lines[index]) or _is_table_row(lines[index]):
                break
            paragraph.append(lines[index])
            index += 1
        content.append({"type": "paragraph", "content": _inline(" ".join(paragraph))})
    if not content:
        content = [{"type": "paragraph", "content": []}]
    return {"type": "doc", "version": 1, "content": content}


def text_of_adf(document: Any) -> str:
    """Rend le texte d'un document ADF — ce que Choregos relit pour trouver son marqueur."""
    if document is None:
        return ""
    if isinstance(document, str):
        return document
    pieces: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for child in node:
                walk(child)
            return
        if not isinstance(node, dict):
            return
        if node.get("type") == "text":
            pieces.append(str(node.get("text", "")))
        children = node.get("content")
        if children:
            walk(children)
            if node.get("type") in {"paragraph", "heading", "tableRow", "listItem"}:
                pieces.append("\n")

    walk(document)
    return "".join(pieces).strip()


def _is_table_row(line: str) -> bool:
    return line.strip().startswith("|") and line.strip().endswith("|")


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _table(lines: list[str], index: int) -> tuple[dict[str, Any], int]:
    header = _cells(lines[index])
    rows: list[dict[str, Any]] = [_row(header, header=True)]
    index += 2  # l'en-tête et sa ligne de séparation
    while index < len(lines) and _is_table_row(lines[index]):
        rows.append(_row(_cells(lines[index])))
        index += 1
    return {"type": "table", "attrs": {"isNumberColumnEnabled": False}, "content": rows}, index


def _row(cells: list[str], *, header: bool = False) -> dict[str, Any]:
    kind = "tableHeader" if header else "tableCell"
    return {
        "type": "tableRow",
        "content": [
            {"type": kind, "attrs": {}, "content": [{"type": "paragraph", "content": _inline(cell)}]}
            for cell in cells
        ],
    }


def _inline(text: str) -> list[dict[str, Any]]:
    """Gras, code et liens ; le reste passe en texte brut."""
    if not text:
        return []
    nodes: list[dict[str, Any]] = []
    position = 0
    pattern = re.compile(f"{BOLD.pattern}|{CODE.pattern}|{LINK.pattern}")
    for match in pattern.finditer(text):
        if match.start() > position:
            nodes.append({"type": "text", "text": text[position : match.start()]})
        bold, code, label, href = match.group(1), match.group(2), match.group(3), match.group(4)
        if bold is not None:
            nodes.append({"type": "text", "text": bold, "marks": [{"type": "strong"}]})
        elif code is not None:
            nodes.append({"type": "text", "text": code, "marks": [{"type": "code"}]})
        else:
            nodes.append(
                {
                    "type": "text",
                    "text": label or href or "",
                    "marks": [{"type": "link", "attrs": {"href": href}}],
                }
            )
        position = match.end()
    if position < len(text):
        nodes.append({"type": "text", "text": text[position:]})
    return nodes or [{"type": "text", "text": text}]
