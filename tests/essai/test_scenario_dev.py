# SPDX-License-Identifier: Apache-2.0
"""The dev scenario does not open an issue the sandbox has already resolved (#706)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]


def _scenario() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "scenario_dev", ROOT / "essai" / "projet-dev" / "scenario_dev.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TITLE = "Credits are not deducted from the invoice total"
MERGED = [
    {"number": 14, "title": "Totals in several currencies", "mergedAt": "2026-10-09T15:08:50Z", "url": "u14"},
    {"number": 17, "title": TITLE, "mergedAt": "2026-10-09T15:39:58Z", "url": "u17"},
]


def test_an_issue_whose_pull_request_was_merged_is_refused() -> None:
    assert _scenario().consumed_by(TITLE, MERGED, None)["number"] == 17


def test_a_merge_before_the_last_reseed_does_not_count() -> None:
    assert _scenario().consumed_by(TITLE, MERGED, "2026-10-17T16:00:00Z") is None


def test_a_merge_after_the_last_reseed_counts() -> None:
    assert _scenario().consumed_by(TITLE, MERGED, "2026-10-09T15:20:00Z")["number"] == 17


def test_another_issue_is_not_refused_for_it() -> None:
    assert _scenario().consumed_by("Decide how invoices are sent", MERGED, None) is None


def test_every_issue_of_the_scenario_is_checked_by_title() -> None:
    scenario = _scenario()
    for _labels, title, _body in scenario.ISSUES.values():
        assert scenario.consumed_by(title, [{"title": title, "mergedAt": "z", "url": "u"}], None)
