# SPDX-License-Identifier: Apache-2.0
"""S47-28: two facts of STATUS.md that the repository can check, kept true.

The 2026-10-27 audit found rows that contradicted the code: S1-08 said the replay histories were
empty while the folder held dozens, and S7-10 said nine runbooks while the folder held more. Both
are facts a test can read, so both are guarded here. The checks are language-neutral: they look at
a row by its id and at its numbers, not at the words around them.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
STATUS = ROOT / "docs" / "plan" / "STATUS.md"
HISTORIES = ROOT / "tests" / "replay" / "histories"
RUNBOOKS = ROOT / "docs" / "runbooks"

#: What a row must not say about a folder that is not empty (the French wording is the one the
#: audit found; the English one is what a translation would write).
EMPTY_WORDS = ("est vide", "is empty")  # lang: fr-ok


def _row(story: str) -> str:
    rows = [
        line for line in STATUS.read_text(encoding="utf-8").splitlines() if line.startswith(f"| {story} | ")
    ]
    assert len(rows) == 1, f"STATUS must carry exactly one row for {story}, found {len(rows)}"
    return rows[0]


def test_the_replay_row_does_not_say_empty() -> None:
    histories = sorted(HISTORIES.glob("*.json"))
    assert histories, "tests/replay/histories is empty: the replay guard has nothing to replay"
    row = _row("S1-08")
    said = [words for words in EMPTY_WORDS if words in row]
    assert not said, f"S1-08 says {said} while {len(histories)} histories are archived"


def _is_stub(path: pathlib.Path) -> bool:
    """A one-line stub left under a former French name starts with `# Moved`."""
    first = path.read_text(encoding="utf-8", errors="replace").splitlines()[:1]
    return first == ["# Moved"]


def test_the_runbook_count_is_the_folder() -> None:
    runbooks = [
        path for path in sorted(RUNBOOKS.glob("*.md")) if path.name != "README.md" and not _is_stub(path)
    ]
    assert runbooks, "docs/runbooks holds no runbook: the folder moved without this test"
    counted = re.search(r"(\d+) runbooks", _row("S7-10"))
    assert counted, "the S7-10 row no longer says how many runbooks there are"
    assert int(counted.group(1)) == len(runbooks), (
        f"S7-10 says {counted.group(1)} runbooks and docs/runbooks holds {len(runbooks)}"
    )
