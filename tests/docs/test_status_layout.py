# SPDX-License-Identifier: Apache-2.0
"""S80-02: STATUS is an index a newcomer can read, and every backlog story has one status.

By 2026-11-16 `docs/plan/STATUS.md` had grown to 2,200 lines and 1.5 MB, with single rows over
12,000 characters: no one could read it, and every PR conflicted on it. S80-02 split it: the page is
an index of the streams, each stream's rows (moved unchanged) are in `docs/plan/status/<stream>.md`,
and the narrative is in `docs/plan/status/history.md`. These checks keep the split from growing back,
and make the backlog and the status files describe the same stories.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
from collections import Counter
from types import ModuleType

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs" / "plan"
STATUS = PLAN / "STATUS.md"
STATUS_DIR = PLAN / "status"
BACKLOG = PLAN / "08-backlog.yaml"

#: The index stays readable: at most this many lines, none longer than this many characters.
MAX_LINES = 300
MAX_LINE_CHARS = 300
#: The legend's statuses: a row with any other symbol is counted by nobody.
STATUSES = {"✅", "🟡", "❌", "⬜"}
#: Streams whose file is not named after them.
FILE_OF = {"owner review": "owner-review.md", "—": "outside-backlog.md"}
LINK = re.compile(r"\]\(status/([^)#\s]+)\)")


def _tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("status_total", ROOT / "tools" / "status_total.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _index() -> list[str]:
    return STATUS.read_text(encoding="utf-8").splitlines()


def test_the_index_is_short() -> None:
    lines = _index()
    assert len(lines) <= MAX_LINES, (
        f"STATUS.md has {len(lines)} lines (at most {MAX_LINES}): a story's row goes in its stream's "
        "file under docs/plan/status/, not in the index"
    )


def test_no_line_of_the_index_is_long() -> None:
    long = [(n, len(line)) for n, line in enumerate(_index(), start=1) if len(line) > MAX_LINE_CHARS]
    assert not long, f"STATUS.md lines over {MAX_LINE_CHARS} characters (line, length): {long}"


def test_the_index_keeps_the_rules_in_sight() -> None:
    text = STATUS.read_text(encoding="utf-8")
    assert "nothing is ✅ without a test that fails in its absence" in text, "the 2026-09-24 rule left"
    assert "## Where a new row goes" in text, "the rule for a new row left the index"
    assert "Legend: ✅" in text, "the legend left the index"


def test_the_index_links_every_stream_file_once() -> None:
    tool = _tool()
    linked = Counter(LINK.findall(STATUS.read_text(encoding="utf-8")))
    files = {path.name for path in tool.stream_files()}
    assert files, "docs/plan/status holds no stream file"
    unlinked = sorted(files - set(linked))
    assert not unlinked, f"stream files the index does not list: {unlinked}"
    dangling = sorted(name for name in linked if not (STATUS_DIR / name).is_file())
    assert not dangling, f"the index links status files that do not exist: {dangling}"
    twice = sorted(name for name, n in linked.items() if n > 1 and name != tool.NARRATIVE)
    assert not twice, f"the index lists these stream files more than once: {twice}"


def test_each_row_sits_in_its_streams_file_with_a_legend_status() -> None:
    misplaced, unknown = [], []
    for row in _tool().rows():
        expected = FILE_OF.get(row.stream, f"{row.stream}.md")
        if row.file.name != expected:
            misplaced.append(f"{row.story} ({row.stream}) in {row.file.name}, expected {expected}")
        if row.status not in STATUSES:
            unknown.append(f"{row.story}: {row.status}")
    assert not misplaced, f"rows outside their stream's file: {misplaced}"
    assert not unknown, f"rows whose status is not one of the legend's {sorted(STATUSES)}: {unknown}"


def test_each_stream_file_has_one_table() -> None:
    tool = _tool()
    for path in tool.stream_files():
        lines = path.read_text(encoding="utf-8").splitlines()
        assert lines.count(tool.HEADER) == 1, f"{path.name}: expected one table header {tool.HEADER!r}"
        start = lines.index(tool.HEADER)
        body = lines[start + 2 :]
        rows = [line for line in body if line.startswith("|")]
        after = body[len(rows) :]
        assert all(not line.strip() for line in after), (
            f"{path.name}: a blank line splits the table, so the rows after it render as text"
        )


def test_the_narrative_holds_no_row() -> None:
    tool = _tool()
    text = (STATUS_DIR / tool.NARRATIVE).read_text(encoding="utf-8")
    rows = [line[:60] for line in text.splitlines() if tool.ROW.match(line)]
    assert not rows, f"status/{tool.NARRATIVE} is not counted, and holds rows: {rows}"


def test_every_backlog_story_has_exactly_one_status() -> None:
    """A story of the backlog with no row has no known state; one with two has two.

    A row whose id ends in `-*` (`S11-*`: the Ecphoria stories, delivered upstream) stands for every
    backlog story of its stream that has no row of its own.
    """
    stories = [story["id"] for story in yaml.safe_load(BACKLOG.read_text("utf-8"))["stories"]]
    rows = _tool().rows()
    exact = Counter(row.story for row in rows)
    wildcards = {row.story[:-1] for row in rows if row.story.endswith("-*")}
    missing, doubled = [], []
    for story in stories:
        covered = exact[story] + sum(1 for prefix in wildcards if story.startswith(prefix))
        if covered == 0:
            missing.append(story)
        elif covered > 1:
            doubled.append(story)
    assert not missing, f"backlog stories with no status row in docs/plan/status/: {missing}"
    assert not doubled, f"backlog stories with more than one status: {doubled}"
