# SPDX-License-Identifier: Apache-2.0
"""A fact without a value is dropped, not held against the result (S62-05, FON-277)."""

from __future__ import annotations

import json
from pathlib import Path

from choregos_runner.result import load_result


def _write(tmp_path: Path, facts: object) -> Path:
    path = tmp_path / "result.json"
    path.write_text(
        json.dumps(
            {
                "status": "done",
                "summary": "triaged",
                "outputs": {"size": "M", "risk": "low", "duplicate_of": None},
                "evidence": {"facts": facts},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_a_null_fact_is_dropped_and_the_result_accepted(tmp_path: Path) -> None:
    loaded = load_result(_write(tmp_path, {"duplicate_of": None, "checked": True}), ["size", "risk"])
    assert loaded.ok, loaded.error
    assert loaded.result is not None and loaded.result.evidence is not None
    assert loaded.result.evidence.facts == {"checked": True}


def test_only_null_facts_leave_no_facts(tmp_path: Path) -> None:
    loaded = load_result(_write(tmp_path, {"duplicate_of": None}), ["size", "risk"])
    assert loaded.ok, loaded.error
    assert loaded.result is not None and loaded.result.evidence is not None
    assert loaded.result.evidence.facts is None


def test_a_fact_of_another_wrong_kind_still_goes_back_to_the_agent(tmp_path: Path) -> None:
    loaded = load_result(_write(tmp_path, {"duplicate_of": {"key": "FON-1"}}), ["size", "risk"])
    assert not loaded.ok
    assert loaded.error is not None and "duplicate_of" in loaded.error
