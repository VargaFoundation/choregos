"""A fact without a value no longer fails an agent written with the SDK (S62-18).

S62-05 made the runner drop a `null` fact from the result file it reads (FON-277, prod, 2026-11-09).
The SDK validates the result with the contract's model before writing it, without that step: an agent
returning `facts={"duplicate_of": None}` raised in `write_result` and its step ended with no result.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from tranibor_agent_sdk.result import Result, write_result


def _facts_written(workspace: Path, facts: dict[str, Any]) -> Any:
    path = write_result(workspace, Result(summary="triaged", facts=facts))
    return json.loads(path.read_text(encoding="utf-8")).get("evidence", {}).get("facts")


def test_a_null_fact_is_dropped_and_the_result_written(tmp_path: Path) -> None:
    assert _facts_written(tmp_path, {"duplicate_of": None, "size": "M"}) == {"size": "M"}


def test_only_null_facts_leave_no_facts(tmp_path: Path) -> None:
    assert _facts_written(tmp_path, {"duplicate_of": None}) is None


def test_a_fact_of_another_wrong_kind_is_still_refused(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        write_result(tmp_path, Result(summary="triaged", facts={"duplicate_of": {"key": "FON-1"}}))  # type: ignore[dict-item]
