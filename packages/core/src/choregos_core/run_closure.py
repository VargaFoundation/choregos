# SPDX-License-Identifier: Apache-2.0
"""When a run is closed, and how one is closed, for the API and the orchestrator alike (ADR 0143).

A run is over for the platform before it is over for its process. The orchestrator stops waiting —
the budget is spent, the queue never admitted it, the executor lost it, a person cancelled — and the
pod, somewhere, may still be alive: the cancel failed, the pod is slow to die, a queued pod starts
late, a partition heals. Its run token cannot be revoked before it expires. So the row
says it, and both sides read the row the same way:

- the orchestrator closes the row when it stops waiting (`close_run`): a terminal status, an end
  date and the result the workflow is told, written together, before the executor is asked to
  cancel anything;
- the run's API treats a closed run as closed (`run_is_closed`): a result posted late is answered
  as a replay and changes nothing, and nothing more is written, minted or served for the run.

"Closed" has one definition, here. A run has no `suspended` status: a run that waits for a person
holds the result that asked the question, so it is closed while its sandbox sleeps, and
`resume_run` is what opens it again (result cleared, status `running`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from choregos_contracts import StageResult

from .domain import utcnow

#: The statuses of a run something still waits for.
OPEN_RUN_STATUSES = frozenset({"queued", "running"})

#: The statuses of a run that ended: every `RunStatus` that is not an open one.
CLOSED_RUN_STATUSES = frozenset({"succeeded", "failed", "cancelled", "timed_out"})


class RunRow(Protocol):
    """What closing reads and writes of a run's row."""

    status: str
    result: dict[str, Any] | None
    ended_at: datetime | None


def run_is_closed(run: RunRow) -> bool:
    """Whether the run ended: it holds a result, or its status is a terminal one.

    Either is enough. A result without a terminal status is a result the orchestrator read from the
    run's journal before this module existed; a terminal status without a result is a run abandoned
    or cancelled before it. A queued or a running run with no result is open, whatever else its
    row holds.
    """
    return run.result is not None or run.status in CLOSED_RUN_STATUSES


def close_run(run: RunRow, result: StageResult, *, status: str, at: datetime | None = None) -> bool:
    """Close `run` with `result` unless a result is already there; True when this call wrote it.

    Replayable (ADR 0008): the first result stays, the runner's or an earlier close's, and a second
    call changes nothing. A run that already ended some way keeps how it ended (`status` is written
    over an open status only) and when (`ended_at`).
    """
    if status not in CLOSED_RUN_STATUSES:
        raise ValueError(f"`{status}` does not close a run: one of {sorted(CLOSED_RUN_STATUSES)}")
    if run.result is not None:
        return False
    run.result = result.model_dump(mode="json", by_alias=True)
    if run.status not in CLOSED_RUN_STATUSES:
        run.status = status
    run.ended_at = run.ended_at or at or utcnow()
    return True


def status_of(result: StageResult) -> str:
    """The status a run takes from its own result: `succeeded` when the stage is done, else `failed`."""
    return "succeeded" if result.ok else "failed"


__all__ = [
    "CLOSED_RUN_STATUSES",
    "OPEN_RUN_STATUSES",
    "RunRow",
    "close_run",
    "run_is_closed",
    "status_of",
]
