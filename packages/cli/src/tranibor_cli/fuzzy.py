# SPDX-License-Identifier: Apache-2.0
"""The palette's fuzzy match (ADR 0187 §1, S67-24): small, pure, no dependency.

A query matches a text when its characters appear in the text in order (case ignored, spaces in the query
skipped): `fnrd` finds `FIN-12 Fix the totals rounding`. The score prefers, in this order, what the text
starts with, characters that follow one another, characters that start a word, and a short text; the ranking
is stable, so equal scores keep the order they were given in.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

_WORD_START = frozenset(" -_/.:·")


def match(query: str, text: str) -> tuple[int, list[int]] | None:
    """`(score, positions)` of the best-scoring way `query` hides in `text`, or `None` when it does not.

    The positions are indexes into `text`, for the caller to highlight. An empty query matches everything
    with score 0.
    """
    wanted = [c for c in query.casefold() if not c.isspace()]
    if not wanted:
        return 0, []
    haystack = text.casefold()
    if len(haystack) != len(text):  # a character that folds to several (ß): match on the text as it is
        haystack = text.lower()
    best: tuple[int, list[int]] | None = None
    # Each occurrence of the first character is tried as the start: the greedy walk from it is the
    # best way to place the others, and trying every start finds the best word to begin in.
    start = haystack.find(wanted[0])
    while start != -1:
        found = _walk(wanted, haystack, text, start)
        if found is not None and (best is None or found[0] > best[0]):
            best = found
        start = haystack.find(wanted[0], start + 1)
    return best


def _walk(wanted: list[str], haystack: str, text: str, start: int) -> tuple[int, list[int]] | None:
    positions: list[int] = []
    at = start
    for char in wanted:
        at = haystack.find(char, at)
        if at == -1:
            return None
        positions.append(at)
        at += 1
    score = 0
    for n, pos in enumerate(positions):
        if pos == 0:
            score += 16
        elif text[pos - 1] in _WORD_START or (text[pos].isupper() and text[pos - 1].islower()):
            score += 8
        if n and pos == positions[n - 1] + 1:
            score += 6
        if n:
            score -= min(pos - (positions[n - 1] + 1), 8)  # a gap between two matches costs, up to a point
    score -= len(text) // 8  # of two equal matches, the shorter text
    return score, positions


def rank[T](query: str, items: Iterable[T], text_of: Callable[[T], str]) -> list[tuple[T, list[int]]]:
    """The items `query` matches, best first (ties in the given order), each with its matched positions."""
    scored: list[tuple[int, int, T, list[int]]] = []
    for order, item in enumerate(items):
        found = match(query, text_of(item))
        if found is not None:
            scored.append((-found[0], order, item, found[1]))
    scored.sort(key=lambda row: (row[0], row[1]))
    return [(item, positions) for _score, _order, item, positions in scored]
