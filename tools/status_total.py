"""Count the stories of the status files by status, from their tables.

STATUS no longer writes its total by hand: every PR that moved a story edited the same line, and two
PRs merged on the same day conflicted on it, which re-ran the whole CI for a line nobody had to write.
The counts are read from the tables, where they already are.

Since S80-02 the rows are not in `docs/plan/STATUS.md` any more: that page is an index of the streams,
and each stream's rows are in `docs/plan/status/<stream>.md` (`history.md` there is the narrative the
page carried until 2026-11-17, and holds no row). This module also reads those rows for the tests.

    uv run python tools/status_total.py
"""

from __future__ import annotations

import pathlib
import re
from typing import NamedTuple

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATUS = ROOT / "docs" / "plan" / "STATUS.md"
STATUS_DIR = ROOT / "docs" / "plan" / "status"
#: The narrative moved out of STATUS.md: dated prose, no row.
NARRATIVE = "history.md"
MARKS = {"delivered": "| ✅ |", "partial": "| 🟡 |", "not started": "| ⬜ |"}
#: `| S40-01a | S40 | 🟡 | #618 | notes…`: the PR cell may be empty (`| |`).
ROW = re.compile(
    r"^\| (?P<story>[^|\s][^|]*?) \| (?P<stream>[^|]+?) \| (?P<status>[^|]+?) \| (?P<pr>[^|]*?) ?\|"
)
HEADER = "| Story | Stream | Status | PR | Notes |"


class Row(NamedTuple):
    file: pathlib.Path
    story: str
    stream: str
    status: str
    pr: str
    line: str


def stream_files() -> list[pathlib.Path]:
    """The files that hold the rows, one per stream."""
    return sorted(path for path in STATUS_DIR.glob("*.md") if path.name != NARRATIVE)


def rows() -> list[Row]:
    """Every story row of every stream file, the table header left out."""
    found: list[Row] = []
    for path in stream_files():
        for line in path.read_text(encoding="utf-8").splitlines():
            match = ROW.match(line)
            if not match or line == HEADER:
                continue
            found.append(
                Row(path, match["story"], match["stream"], match["status"], match["pr"].strip(), line)
            )
    return found


def rows_text() -> str:
    """The stream files' text, end to end."""
    return "\n".join(path.read_text(encoding="utf-8") for path in stream_files())


def counts(text: str) -> dict[str, int]:
    """Each status's number of rows."""
    return {name: text.count(mark) for name, mark in MARKS.items()}


def main() -> None:
    found = counts(rows_text())
    print(", ".join(f"{n} {name}" for name, n in found.items()))


if __name__ == "__main__":
    main()
