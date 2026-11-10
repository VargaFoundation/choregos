"""STATUS's total is read from its table, never written by hand.

`docs/plan/STATUS.md` claimed "Total: 96 delivered, 6 partial" under a table that counted 92 and
10. The figure was copied by hand and had stopped following; it was then checked here, and every PR
that moved a story had to rewrite the same line. Two PRs merged on the same day conflicted on it, and
each conflict re-ran a CI of several hours (#1523, 2026-11-10). The line now names the command that
counts (`tools/status_total.py`), and carries no figure that could drift or conflict.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
STATUS = ROOT / "docs" / "plan" / "STATUS.md"
TOTAL_LINE = re.compile(r"^\*\*Total\*\*.*$", re.M)


def _counts(text: str) -> dict[str, int]:
    spec = importlib.util.spec_from_file_location("status_total", ROOT / "tools" / "status_total.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return dict(module.counts(text))


def test_the_total_line_names_the_count_and_holds_no_figure() -> None:
    text = STATUS.read_text(encoding="utf-8")
    lines = TOTAL_LINE.findall(text)
    assert len(lines) == 1, f"STATUS must have one Total line, it has {len(lines)}: {lines}"
    assert "tools/status_total.py" in lines[0], "the Total line no longer says how to count"
    assert not re.search(r"\d", lines[0]), f"a figure came back on the Total line: {lines[0]}"


def test_the_table_is_counted() -> None:
    found = _counts(STATUS.read_text(encoding="utf-8"))
    assert found["delivered"] > 0, "no delivered story counted: the table changed shape"
    assert set(found) == {"delivered", "partial", "not started"}


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
