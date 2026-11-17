# SPDX-License-Identifier: Apache-2.0
"""S80-01: every partial story has a verdict in the triage of ADR 0180.

The scope freeze of 2026-11-17 sorts each story marked 🟡 in STATUS into one of three piles:
finish (naming the workstream that closes it), frozen by name, or abandon. A list that misses a
story is a story nobody decided about, so this test reads every 🟡 row of STATUS and refuses one
that `docs/plan/TRIAGE-2026-11-17.md` does not name, a pile outside the three, and a story named
twice. A story that has become ✅ may keep its row.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
from types import ModuleType

ROOT = pathlib.Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs" / "plan"
TRIAGE = PLAN / "TRIAGE-2026-11-17.md"
#: `| S27-02 | finish (depth) | reason |`, the shape of a triage row.
TRIAGE_ROW = re.compile(r"^\| (S\d+-[0-9A-Za-z]+) \| ([^|]+?) \| ([^|]*?) \|$", re.M)
PILE = re.compile(r"^(?:finish \((?:S[1-6]|P[1-4]|A[1-6]|O[1-4]|depth)\)|frozen|abandon)$")


def _status_tool() -> ModuleType:
    """`tools/status_total.py` reads the rows of `docs/plan/status/<stream>.md` (S80-02)."""
    spec = importlib.util.spec_from_file_location("status_total", ROOT / "tools" / "status_total.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _partial_stories() -> set[str]:
    return {row.story for row in _status_tool().rows() if row.status == "🟡"}


def _triage() -> list[tuple[str, str, str]]:
    return TRIAGE_ROW.findall(TRIAGE.read_text("utf-8"))


def test_status_still_has_partial_rows_to_read() -> None:
    """If the row shape changes, the other tests would pass on an empty set."""
    assert len(_partial_stories()) > 50, "too few 🟡 rows found: STATUS changed shape without this test"


def test_every_partial_story_is_triaged() -> None:
    triaged = {sid for sid, _, _ in _triage()}
    missing = sorted(_partial_stories() - triaged)
    assert not missing, (
        "🟡 stories without a verdict in docs/plan/TRIAGE-2026-11-17.md (ADR 0180): add a row "
        f"'| <story> | finish (<workstream>) / frozen / abandon | <reason> |' for each: {missing}"
    )


def test_each_row_has_one_of_the_three_piles_and_a_reason() -> None:
    bad = [f"{sid}: {pile!r}" for sid, pile, _ in _triage() if not PILE.match(pile)]
    assert not bad, f"piles outside finish (<workstream>), frozen, abandon: {bad}"
    silent = [sid for sid, _, reason in _triage() if not reason.strip()]
    assert not silent, f"triage rows without a reason: {silent}"


def test_no_story_is_triaged_twice() -> None:
    ids = [sid for sid, _, _ in _triage()]
    twice = sorted({sid for sid in ids if ids.count(sid) > 1})
    assert not twice, f"stories with more than one verdict: {twice}"
