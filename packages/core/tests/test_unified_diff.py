# SPDX-License-Identifier: Apache-2.0
"""A branch's diff as one text: headers rebuilt, bounded, and no secret in it (ADR 0129 §7, §8)."""

from __future__ import annotations

import pytest
from choregos_core.domain import DiffFile, DiffSummary
from choregos_core.unified_diff import CUT, NO_PATCH, WITHHELD, unified_diff

HUNK = "@@ -1,2 +1,2 @@\n-old\n+new\n ctx"


def _summary(*files: DiffFile) -> DiffSummary:
    return DiffSummary(base="main", head="work", files=list(files))


def test_a_branch_that_changes_nothing_has_no_diff() -> None:
    assert unified_diff(_summary(), 1000) is None


def test_each_file_gets_the_header_the_scm_leaves_out() -> None:
    diff = unified_diff(
        _summary(
            DiffFile(path="a.py", additions=1, deletions=1, patch=HUNK),
            DiffFile(path="new.py", status="added", additions=1, patch="@@ -0,0 +1 @@\n+x"),
            DiffFile(path="gone.py", status="removed", deletions=1, patch="@@ -1 +0,0 @@\n-x"),
            DiffFile(path="b.py", status="renamed", old_path="c.py", patch=HUNK),
        ),
        10_000,
    )
    assert diff is not None
    assert diff.files == 4
    assert (diff.additions, diff.deletions) == (2, 2)
    assert (
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n-old\n+new\n ctx\n" in diff.text
    )
    assert "--- /dev/null\n+++ b/new.py" in diff.text
    assert "--- a/gone.py\n+++ /dev/null" in diff.text
    assert "diff --git a/c.py b/b.py\nrename from c.py\nrename to b.py\n--- a/c.py\n+++ b/b.py" in diff.text


def test_a_header_repeated_in_the_patch_is_not_written_twice() -> None:
    diff = unified_diff(_summary(DiffFile(path="a.py", patch=f"--- a/a.py\n+++ b/a.py\n{HUNK}")), 10_000)
    assert diff is not None
    assert diff.text.count("--- a/a.py") == 1


def test_a_file_the_scm_gives_no_patch_for_says_so() -> None:
    diff = unified_diff(_summary(DiffFile(path="logo.png", patch=None)), 10_000)
    assert diff is not None
    assert NO_PATCH in diff.text


def test_a_diff_too_large_is_cut_file_by_file_and_says_it() -> None:
    big = "@@ -1 +1 @@\n+" + "x" * 500
    diff = unified_diff(
        _summary(
            DiffFile(path="a.py", patch=HUNK),
            DiffFile(path="big.py", patch=big),
            DiffFile(path="z.py", patch=HUNK),
        ),
        100,
    )
    assert diff is not None
    assert diff.truncated
    assert CUT in diff.text
    assert "x" * 500 not in diff.text
    assert diff.text.count("-old") == 2  # a later small file still fits


def test_a_secret_withholds_the_patch_of_its_own_file_and_names_only_its_shape() -> None:
    key = "AKIA" + "ABCDEFGHIJKLMNOP"
    diff = unified_diff(
        _summary(
            DiffFile(path="a.py", additions=1, deletions=1, patch=HUNK),
            DiffFile(path="b.py", additions=1, patch=f"@@ -0,0 +1 @@\n+{key}"),
        ),
        10_000,
    )
    assert diff is not None
    assert diff.withheld == ["AWS key"]
    assert key not in diff.text
    assert diff.text.count(WITHHELD) == 1
    assert "diff --git a/b.py b/b.py" in diff.text
    assert "+new" in diff.text, "a file the scan found clean stays readable"


@pytest.mark.parametrize(
    ("line", "shape"),
    [
        ("+url: github_pat" + "_11ABCDEFGHIJKLMNOPQRSTUVWX", "GitHub token"),
        ("+glpat" + "-abcdefghij1234567890", "GitLab token"),
        ("+-----BEGIN ENCRYPTED PRIVATE KEY-----", "private key"),
        ('+DB_PASSWORD="hunter2-very-secret-value"', "secret assignment"),
        ("+  api_key: '" + "abcdefgh" + "12345678'", "secret assignment"),
    ],
)
def test_the_shapes_a_diff_must_never_keep(line: str, shape: str) -> None:
    diff = unified_diff(_summary(DiffFile(path="a.py", patch=f"@@ -0,0 +1 @@\n{line}")), 10_000)
    assert diff is not None
    assert shape in diff.withheld
    assert line[1:] not in diff.text


@pytest.mark.parametrize(
    "line",
    [
        '+token = os.environ["GITHUB_TOKEN"]',
        "+  password: ${{ secrets.DB_PASSWORD }}",
        "+tokens_in: 1200",
        '-DB_PASSWORD="hunter2-very-secret-value"x',
    ],
)
def test_code_that_only_names_a_secret_stays_readable(line: str) -> None:
    patch = f"@@ -0,0 +1 @@\n{line}"
    if line.startswith("-"):
        patch = f"@@ -1 +0,0 @@\n{line.replace('="', '= ')}"
    diff = unified_diff(_summary(DiffFile(path="a.py", patch=patch)), 10_000)
    assert diff is not None
    assert diff.withheld == []


def test_a_secret_in_a_path_is_kept_out_of_the_header_too() -> None:
    key = "AKIA" + "ABCDEFGHIJKLMNOP"
    diff = unified_diff(_summary(DiffFile(path=f"keys/{key}.txt", patch=HUNK)), 10_000)
    assert diff is not None
    assert diff.withheld == ["AWS key"]
    assert key not in diff.text


def test_a_withheld_patch_still_changes_when_the_branch_does() -> None:
    key = "AKIA" + "ABCDEFGHIJKLMNOP"
    first = unified_diff(_summary(DiffFile(path="a.py", additions=5, patch=f"@@ -0,0 +1 @@\n+{key}")), 10_000)
    second = unified_diff(
        _summary(DiffFile(path="a.py", additions=50, patch=f"@@ -0,0 +1 @@\n+{key}")), 10_000
    )
    assert first is not None and second is not None
    assert first.text != second.text, "the same text would make no new revision"


def test_a_pure_rename_has_no_patch_to_miss() -> None:
    diff = unified_diff(_summary(DiffFile(path="b.py", old_path="a.py", status="renamed")), 10_000)
    assert diff is not None
    assert NO_PATCH not in diff.text


def test_a_patch_with_no_hunk_is_no_patch() -> None:
    diff = unified_diff(
        _summary(DiffFile(path="logo.png", patch="Binary files a/logo.png and b/logo.png differ")), 10_000
    )
    assert diff is not None
    assert NO_PATCH in diff.text
    assert "Binary files" not in diff.text
