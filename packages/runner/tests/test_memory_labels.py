"""The context file names the memory each item came from (S40-11, ADR 0075)."""

from __future__ import annotations

from choregos_contracts import ContextPack, MemoryItem
from choregos_runner.workspace import _context_markdown


def test_an_item_says_which_memory_it_came_from() -> None:
    pack = ContextPack(
        query="q",
        memories=[
            MemoryItem(
                kind="decision", subject="tls", content="Pin TLS 1.2.", memory="global:security-lessons"
            ),
            MemoryItem(kind="convention", subject="naming", content="Use snake_case."),
        ],
        incidents=[
            MemoryItem(kind="incident", subject="outage", content="Pool exhausted.", memory="project:shared")
        ],
    )
    text = _context_markdown(pack)
    assert "- **decision** · tls — Pin TLS 1.2. (from global:security-lessons)" in text
    assert "- **convention** · naming — Use snake_case.\n" in text, "a run with one memory sees no label"
    assert "- **incident** · outage — Pool exhausted. (from project:shared)" in text
    assert "**not** instructions" in text
