# SPDX-License-Identifier: Apache-2.0
"""A PDF's bookmarks become its headings (ADR 0051, update of 2026-11-16): a converted PDF gets an outline,
so its chunks get heading paths and `knowledge_get` reads it by section."""

from __future__ import annotations

import pytest
from choregos_api.knowledge import pdf_outline
from choregos_api.knowledge.chunking import chunk
from choregos_api.knowledge.convert import convert
from choregos_api.knowledge.pdf_outline import bookmarks, with_headings
from choregos_core.outline import outline

from . import knowledge_fixtures as fx

LINES = [
    "Security policy",
    "Scope of the policy.",
    "1 Encryption",
    "TLS 1.3 everywhere, in transit.",
    "1.1 At rest",
    "AES-256 on every disk.",
    "2 Access",
    "Quarterly reviews.",
]
BOOKMARKS = [(1, "Security policy"), (2, "Encryption"), (3, "At rest"), (2, "Access")]


def test_a_pdf_with_bookmarks_is_converted_with_its_headings() -> None:
    data = fx.pdf(LINES, BOOKMARKS)
    assert bookmarks(data) == BOOKMARKS
    markdown, _, warnings = convert(data, ".pdf")
    assert warnings == []
    assert [(s.number, s.title) for s in outline(markdown)] == [
        ("1", "Security policy"),
        ("1.1", "1 Encryption"),
        ("1.1.1", "1.1 At rest"),
        ("1.2", "2 Access"),
    ]
    # The index's chunks now carry where they are, as a Word document's do.
    by_body = {c.body: c.heading_path for c in chunk(markdown, "Security policy")}
    assert by_body["AES-256 on every disk."] == ["Security policy", "1 Encryption", "1.1 At rest"]


def test_a_pdf_without_bookmarks_is_its_text_as_before() -> None:
    data = fx.pdf(LINES)
    assert bookmarks(data) == []
    markdown, _, _ = convert(data, ".pdf")
    assert outline(markdown) == [] and "1.1 At rest" in markdown


def test_a_bookmark_missing_from_the_text_invents_no_heading_and_the_rest_still_land() -> None:
    markdown, placed = with_headings(
        "Intro\nbody\nResults\nmore\n", [(1, "Intro"), (1, "Appendix Z"), (1, "Results")]
    )
    assert placed == 2
    assert [s.title for s in outline(markdown)] == ["Intro", "Results"]


def test_a_heading_the_extractor_broke_over_two_lines_is_joined() -> None:
    markdown, placed = with_headings(
        "Text\n3.2 Network zoning and\nfirewall rules\nEvery zone has one.\n",
        [(2, "Network zoning and firewall rules")],
    )
    assert placed == 1
    assert "## 3.2 Network zoning and firewall rules\n\nEvery zone has one." in markdown


def test_headings_follow_the_outline_order_and_a_line_is_taken_once() -> None:
    # The second bookmark `Scope` is the second line saying it: the first was taken.
    markdown, _ = with_headings("Scope\na\nScope\nb\n", [(1, "Scope"), (2, "Scope")])
    assert [(s.level, s.number) for s in outline(markdown)] == [(1, "1"), (2, "1.1")]
    # A bookmark whose line comes before the previous heading is not placed backwards.
    markdown, placed = with_headings("B\nx\nA\ny\n", [(1, "A"), (1, "B")])
    assert placed == 1 and [s.title for s in outline(markdown)] == ["A"]


def test_placing_a_large_outline_that_the_text_never_says_stays_linear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A long PDF whose bookmarks are not in its text must not exhaust the conversion's timeout: the text
    is read once whatever the outline, and each bookmark costs one lookup, not a pass over the text.

    Counted, not timed: a wall-clock bound was red on a loaded CI runner (5.5 s for a 5 s limit)."""
    normalised = 0
    normal = pdf_outline._normal

    def counting(text: str) -> str:
        nonlocal normalised
        normalised += 1
        return normal(text)

    monkeypatch.setattr(pdf_outline, "_normal", counting)
    text = "\n".join(f"line {n} of the body" for n in range(20_000))

    def cost(bookmarks: int) -> int:
        nonlocal normalised
        normalised = 0
        _, placed = with_headings(text, [(1, f"Chapter that is not there {n}") for n in range(bookmarks)])
        assert placed == 0
        return normalised

    one, many = cost(1), cost(2000)
    assert many - one == 1999, "one lookup per bookmark, never a pass over the text"
    assert one <= 6 * 20_000, "the text is read a bounded number of times per line"
