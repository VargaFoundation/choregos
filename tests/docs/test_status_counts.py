"""Is the total announced by STATUS the one of its own table?

`docs/plan/STATUS.md` claimed "Total: 96 delivered, 6 partial" under a table that counted 92 and
10. The figure was copied by hand and had stopped following. It is the kind of gap that hurts
nobody the day it is born, and that makes the document unusable the day someone relies on it to
decide.

The total is now checked, not re-read.
"""

from __future__ import annotations

import pathlib
import re

STATUS = pathlib.Path(__file__).resolve().parents[2] / "docs" / "plan" / "STATUS.md"
# The French total line of STATUS.md is parsed as it is written (rewritten by S45-36).
TOTAL = re.compile(
    r"\*\*Total\*\* : (\d+) livrées, (\d+) partielles, (\d+) non commencée",  # lang: fr-ok S45-36
    re.M,
)


def test_the_total_is_the_one_of_the_table() -> None:
    text = STATUS.read_text(encoding="utf-8")
    announced = TOTAL.search(text)
    assert announced, "STATUS announces no total any more, or its shape changed without this test"
    count = (text.count("| ✅ |"), text.count("| 🟡 |"), text.count("| ⬜ |"))
    assert count[0] > 0, "no delivered story counted: the table changed shape"
    assert tuple(int(n) for n in announced.groups()) == count, (
        f"STATUS announces {announced.groups()} and its table says {count}"
    )


STORY_ROW = re.compile(r"^\| ([A-Z][A-Za-z0-9-]+) \| ", re.M)


def test_each_story_has_one_row() -> None:
    """A rebase that keeps both sides leaves a story twice, and the total counts it twice (#794)."""
    ids = STORY_ROW.findall(STATUS.read_text(encoding="utf-8"))
    assert len(ids) > 100, "the table no longer parses: its row shape changed without this test"
    twice = sorted({story for story in ids if ids.count(story) > 1} - {"Story"})
    assert not twice, f"STATUS has these stories on more than one row: {twice}"


def test_no_story_row_after_the_total() -> None:
    """A conflict resolution that appends the other side after the Total leaves rows outside the table."""
    lines = STATUS.read_text(encoding="utf-8").splitlines()
    total = next(i for i, line in enumerate(lines) if line.startswith("**Total**"))
    after = [line.split(" | ")[0] for line in lines[total + 1 :] if STORY_ROW.match(line + "\n")]
    assert not after, f"these STATUS rows sit below the Total, outside the table: {after}"
