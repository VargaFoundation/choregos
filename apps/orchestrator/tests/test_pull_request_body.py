"""The pull request body is built from the core's list of sections (S39-13a).

The map's "in the pull request" flag reads the same `PULL_REQUEST_SECTIONS`: they cannot disagree.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from choregos_core.dsl.flow import PULL_REQUEST_SECTIONS
from choregos_orchestrator.activities.scm import _pr_body

ITEM = SimpleNamespace(tracker_key="varga/billing-api#7", body_snapshot="The total ignores credits.")


def test_one_section_per_document_present_in_the_declared_order() -> None:
    documents = {name: f"text of {name}" for name, _ in PULL_REQUEST_SECTIONS}
    body = _pr_body(ITEM, documents)
    positions = [body.index(f"## {title}\ntext of {name}") for name, title in PULL_REQUEST_SECTIONS if title]
    assert positions == sorted(positions)
    assert body.startswith("Work item: varga/billing-api#7\n\ntext of spec_markdown\n")


@pytest.mark.parametrize(("name", "title"), [(n, t) for n, t in PULL_REQUEST_SECTIONS if t])
def test_an_absent_document_leaves_no_section(name: str, title: str) -> None:
    documents = {n: "x" for n, _ in PULL_REQUEST_SECTIONS if n != name}
    assert f"## {title}" not in _pr_body(ITEM, documents)


def test_the_work_item_body_stands_in_for_a_missing_spec() -> None:
    assert "The total ignores credits." in _pr_body(ITEM, {})
