"""STATUS's total is read from its tables, never written by hand.

`docs/plan/STATUS.md` claimed "Total: 96 delivered, 6 partial" under a table that counted 92 and
10. The figure was copied by hand and had stopped following; it was then checked here, and every PR
that moved a story had to rewrite the same line. Two PRs merged on the same day conflicted on it, and
each conflict re-ran a CI of several hours (#1523, 2026-11-10). The line now names the command that
counts (`tools/status_total.py`), and carries no figure that could drift or conflict.

Since S80-02 the rows live in one file per stream (`docs/plan/status/<stream>.md`) and STATUS.md is
their index: the Total line stays on the index, the rows are counted across the stream files.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
from types import ModuleType

ROOT = pathlib.Path(__file__).resolve().parents[2]
STATUS = ROOT / "docs" / "plan" / "STATUS.md"
TOTAL_LINE = re.compile(r"^\*\*Total\*\*.*$", re.M)


def _tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("status_total", ROOT / "tools" / "status_total.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_total_line_names_the_count_and_holds_no_figure() -> None:
    text = STATUS.read_text(encoding="utf-8")
    lines = TOTAL_LINE.findall(text)
    assert len(lines) == 1, f"STATUS must have one Total line, it has {len(lines)}: {lines}"
    assert "tools/status_total.py" in lines[0], "the Total line no longer says how to count"
    assert not re.search(r"\d", lines[0]), f"a figure came back on the Total line: {lines[0]}"


def test_the_tables_are_counted() -> None:
    tool = _tool()
    found = dict(tool.counts(tool.rows_text()))
    assert found["delivered"] > 0, "no delivered story counted: the tables changed shape"
    assert set(found) == {"delivered", "partial", "not started"}


def test_each_story_has_one_row() -> None:
    """A rebase that keeps both sides leaves a story twice, and the total counts it twice (#794).

    Since S80-02 the same story could also land in two stream files: the check is across all of them.
    """
    ids = [row.story for row in _tool().rows()]
    assert len(ids) > 100, "the tables no longer parse: their row shape changed without this test"
    twice = sorted({story for story in ids if ids.count(story) > 1})
    assert not twice, f"the status files have these stories on more than one row: {twice}"


def test_the_index_holds_no_story_row() -> None:
    """A conflict resolution that appends a row to the index leaves it outside every stream's table."""
    tool = _tool()
    lines = STATUS.read_text(encoding="utf-8").splitlines()
    stray = [
        line[:60] for line in lines if tool.ROW.match(line) and not line.startswith(("| Stream |", "| ["))
    ]
    assert not stray, f"these story rows sit in STATUS.md, outside every stream file: {stray}"
