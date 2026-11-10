"""Count the stories of `docs/plan/STATUS.md` by status, from its table.

STATUS no longer writes its total by hand: every PR that moved a story edited the same line, and two
PRs merged on the same day conflicted on it, which re-ran the whole CI for a line nobody had to write.
The counts are read from the table, where they already are.

    uv run python tools/status_total.py
"""

from __future__ import annotations

import pathlib

STATUS = pathlib.Path(__file__).resolve().parents[1] / "docs" / "plan" / "STATUS.md"
MARKS = {"delivered": "| ✅ |", "partial": "| 🟡 |", "not started": "| ⬜ |"}


def counts(text: str) -> dict[str, int]:
    """Each status's number of rows."""
    return {name: text.count(mark) for name, mark in MARKS.items()}


def main() -> None:
    found = counts(STATUS.read_text(encoding="utf-8"))
    print(", ".join(f"{n} {name}" for name, n in found.items()))


if __name__ == "__main__":
    main()
