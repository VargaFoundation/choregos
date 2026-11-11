"""When a run is closed, and what closing one writes (S65-01, ADR 0143).

The orchestrator used to stop waiting for a run without saying so on its row: a status at best,
never a result. The run's API read "no result" as "open", so a process that outlived its run could
still post one, and the run came back to life behind the attempt that had replaced it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from choregos_contracts import RunStatus, StageResult, StageStatus
from choregos_core.run_closure import (
    CLOSED_RUN_STATUSES,
    OPEN_RUN_STATUSES,
    close_run,
    run_is_closed,
    status_of,
)

TIMED_OUT = StageResult.failure("over 30 min", "timeout")
DONE = StageResult(status=StageStatus.DONE, summary="fix applied")


@dataclass
class Row:
    """A run's row, as closing reads and writes it."""

    status: str = "running"
    result: dict[str, Any] | None = None
    ended_at: datetime | None = None


def test_every_run_status_is_either_open_or_closed() -> None:
    """A status added to the contract must be put on one side here, not left to chance."""
    assert frozenset({"queued", "running"}) == OPEN_RUN_STATUSES
    assert {str(status) for status in RunStatus} == OPEN_RUN_STATUSES | CLOSED_RUN_STATUSES
    assert not OPEN_RUN_STATUSES & CLOSED_RUN_STATUSES


@pytest.mark.parametrize("status", sorted(OPEN_RUN_STATUSES))
def test_a_queued_or_running_run_is_open(status: str) -> None:
    assert not run_is_closed(Row(status=status)), "something still waits for this run"


@pytest.mark.parametrize("status", sorted(CLOSED_RUN_STATUSES))
def test_a_terminal_status_closes_a_run_even_without_a_result(status: str) -> None:
    assert run_is_closed(Row(status=status)), "abandoned or cancelled before a result was written"


def test_a_result_closes_a_run_whatever_its_status_says() -> None:
    posted = DONE.model_dump(mode="json", by_alias=True)
    assert run_is_closed(Row(status="running", result=posted))


def test_closing_writes_the_status_the_date_and_the_result_together() -> None:
    row, at = Row(status="running"), datetime(2026, 11, 11, 9, 30, tzinfo=UTC)
    assert close_run(row, TIMED_OUT, status="failed", at=at) is True
    assert (row.status, row.ended_at) == ("failed", at)
    assert StageResult.model_validate(row.result) == TIMED_OUT
    assert run_is_closed(row)


def test_closing_twice_changes_nothing() -> None:
    """ADR 0008: a replayed activity finds its run closed, and leaves it as it closed it."""
    row, first = Row(status="queued"), datetime(2026, 11, 11, 9, 30, tzinfo=UTC)
    close_run(row, TIMED_OUT, status="failed", at=first)
    kept = (row.status, row.ended_at, dict(row.result or {}))
    later = StageResult.failure("cancelled", "cancelled")
    assert close_run(row, later, status="cancelled", at=datetime(2026, 11, 12, tzinfo=UTC)) is False
    assert (row.status, row.ended_at, row.result) == kept


def test_a_result_the_runner_posted_first_is_never_overwritten() -> None:
    posted, at = DONE.model_dump(mode="json", by_alias=True), datetime(2026, 11, 11, 9, 0, tzinfo=UTC)
    row = Row(status="succeeded", result=dict(posted), ended_at=at)
    assert close_run(row, TIMED_OUT, status="failed") is False
    assert (row.status, row.result, row.ended_at) == ("succeeded", posted, at)


def test_a_run_that_already_ended_keeps_how_it_ended_and_gains_the_result() -> None:
    """A row abandoned before results were written on abandon: `failed`, no result."""
    at = datetime(2026, 11, 1, tzinfo=UTC)
    row = Row(status="failed", ended_at=at)
    assert close_run(row, StageResult.failure("cancelled", "cancelled"), status="cancelled") is True
    assert (row.status, row.ended_at) == ("failed", at), "how and when it ended stay"
    assert (row.result or {})["reason"] == "cancelled"


@pytest.mark.parametrize("status", [*sorted(OPEN_RUN_STATUSES), "suspended", ""])
def test_only_a_terminal_status_closes(status: str) -> None:
    row = Row()
    with pytest.raises(ValueError, match="does not close a run"):
        close_run(row, TIMED_OUT, status=status)
    assert row == Row(), "nothing was written"


def test_a_run_takes_its_status_from_its_own_result() -> None:
    assert status_of(DONE) == "succeeded"
    assert status_of(TIMED_OUT) == "failed"
    assert status_of(StageResult(status=StageStatus.NEEDS_HUMAN, summary="which currency?")) == "failed"
