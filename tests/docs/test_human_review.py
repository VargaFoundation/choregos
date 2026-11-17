# SPDX-License-Identifier: Apache-2.0
"""S80-01: contracts, migrations and security guards are owned for human review (ADR 0180 §3).

Until a second human reviewer joins, nothing blocks a merge: the mechanism is that `CODEOWNERS`
names the paths and the pull request template asks who read the change. Two drifts would make it
silent: a path that moves (a rename, ADR 0176) and leaves its line pointing at nothing, and a later
line that takes the path back from the reviewers (in `CODEOWNERS` the last matching line wins).
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
CODEOWNERS = ROOT / "CODEOWNERS"
TEMPLATE = ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"
OWNER_SUFFIX = "/human-review"
#: The three areas of ADR 0180 §3: each must keep at least one line.
AREAS = (
    "/packages/contracts/",
    "/apps/api/src/choregos_api/migrations/",
    "/apps/api/src/choregos_api/security.py",
)


def _rules() -> list[tuple[str, list[str]]]:
    rules = []
    for line in CODEOWNERS.read_text("utf-8").splitlines():
        parts = line.split()
        if parts and not parts[0].startswith("#"):
            rules.append((parts[0], parts[1:]))
    return rules


def _matches(pattern: str, path: str) -> bool:
    """The subset of CODEOWNERS patterns this file uses: `*`, an anchored directory, an anchored file."""
    if pattern == "*":
        return True
    if pattern.endswith("/"):
        return path.startswith(pattern)
    return path == pattern


def _reviewed() -> list[str]:
    return [pattern for pattern, owners in _rules() if any(o.endswith(OWNER_SUFFIX) for o in owners)]


def test_the_three_areas_are_owned_for_human_review() -> None:
    reviewed = _reviewed()
    missing = [area for area in AREAS if area not in reviewed]
    assert not missing, f"areas of ADR 0180 §3 without a human-review line in CODEOWNERS: {missing}"


def test_every_reviewed_path_exists() -> None:
    gone = [pattern for pattern in _reviewed() if not (ROOT / pattern.lstrip("/")).exists()]
    assert not gone, f"CODEOWNERS gives human review to paths that do not exist (moved?): {gone}"


def test_no_later_line_takes_a_reviewed_path_back() -> None:
    rules = _rules()
    taken = []
    for index, (pattern, owners) in enumerate(rules):
        if not any(o.endswith(OWNER_SUFFIX) for o in owners):
            continue
        for later, later_owners in rules[index + 1 :]:
            if _matches(later, pattern) and not any(o.endswith(OWNER_SUFFIX) for o in later_owners):
                taken.append(f"{pattern} (by {later})")
    assert not taken, f"later CODEOWNERS lines take these paths away from human review: {taken}"


def test_the_pull_request_asks_who_read_it() -> None:
    text = TEMPLATE.read_text("utf-8")
    assert "## Human review (ADR 0180)" in text
    assert "human-review-owed" in text, "the template no longer names the label of an owed review"
