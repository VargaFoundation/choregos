# SPDX-License-Identifier: Apache-2.0
"""The Jira converter ends on any input (S62-06).

On the prod tenant on 2026-11-10, a status comment whose table row was broken by a multi-line run
summary sent `markdown_to_adf` into a loop: a line that looks like a table row, with no separator
under it, stopped the paragraph before it took any line, and the outer loop appended empty
paragraphs until the tracker worker was OOM-killed (FON-277)."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

import pytest
from choregos_adapters.tracker.adf import markdown_to_adf, text_of_adf

_CONVERT = """
import json, resource, sys
resource.setrlimit(resource.RLIMIT_AS, (1024 * 2**20, 1024 * 2**20))
from choregos_adapters.tracker.adf import markdown_to_adf
print(json.dumps(markdown_to_adf(sys.stdin.read())))
"""


def _converted(markdown: str) -> dict[str, Any]:
    """In a child process capped at 1 GiB and 30 s: a loop fails this test, not the whole run."""
    done = subprocess.run(
        [sys.executable, "-c", _CONVERT],
        input=markdown,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-500:]
    result: dict[str, Any] = json.loads(done.stdout)
    return result


BROKEN = (
    "<!-- choregos:status -->\n### Choregos — progress\n\n"
    "| # | Step | Result |\n|--:|:--|:--|\n"
    "| 1 | Triaged | the result does not follow the contract:\n"
    "- `evidence.facts.duplicate_of.str`: Input should be  |\n"
    "| | **Total** | |\n\nRun: https://example/p/iris/items/1\n"
)


def test_the_status_comment_that_killed_the_tracker_converts() -> None:
    document = _converted(BROKEN)
    text = text_of_adf(document)
    assert "Total" in text and "duplicate_of" in text
    assert len(document["content"]) < 10


@pytest.mark.parametrize(
    "markdown",
    ["| lone | row |", "text\n| lone | row |\nmore", "| a | b |\n| c | d |", "- x\n| a |\n- y"],
)
def test_a_table_row_without_a_table_is_text(markdown: str) -> None:
    document = _converted(markdown)
    assert all(node["type"] != "table" for node in document["content"])
    assert "a" in text_of_adf(document) or "lone" in text_of_adf(document)


def test_an_escaped_pipe_stays_in_its_cell() -> None:
    document = markdown_to_adf("| k | v |\n|--|--|\n| a \\| b | c |\n")
    row = document["content"][0]["content"][1]["content"]
    assert len(row) == 2
    assert row[0]["content"][0]["content"][0]["text"] == "a | b"
