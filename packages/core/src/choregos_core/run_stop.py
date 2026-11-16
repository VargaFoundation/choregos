# SPDX-License-Identifier: Apache-2.0
"""A run a person stopped (ADR 0142 §2): the result the API writes, and how the interpreter reads it.

One definition for both sides: the API writes the result when a person stops the run, the
interpreter recognises it after `await_run` and takes the stage to a person instead of retrying it.
The result is an ordinary `StageResult` (`failed`, the reason below, `agent_exit: cancelled`): no
contract changes, and every reader that does not know it sees a failed stage.
"""

from __future__ import annotations

from typing import Any

from choregos_contracts import StageResult, StageStatus
from choregos_contracts.stage import Diagnostics

#: The `reason` of a run a person stopped. Distinct from the executor's `cancelled` state, which
#: `await_run` reports for a run that was cancelled without anyone writing its result.
STOPPED_BY_A_PERSON = "stopped_by_person"

#: The longest summary a `StageResult` holds.
_SUMMARY_MAX = 4000


def stopped_summary(by: str, reason: str) -> str:
    """What the work item says while it waits: "run stopped by <who>: <reason>"."""
    return f"run stopped by {by or 'a person'}: {reason.strip() or 'no reason given'}"[:_SUMMARY_MAX]


def stopped_result(by: str, reason: str) -> StageResult:
    """The result written in place of the runner's, when a person stops the run."""
    return StageResult(
        status=StageStatus.FAILED,
        reason=STOPPED_BY_A_PERSON,
        summary=stopped_summary(by, reason),
        diagnostics=Diagnostics(agent_exit="cancelled"),
    )


def stopped_by_a_person(result: StageResult | dict[str, Any] | None) -> bool:
    """Whether a run's result is the one `stopped_result` writes."""
    if result is None:
        return False
    if isinstance(result, dict):
        return result.get("reason") == STOPPED_BY_A_PERSON and result.get("status") != StageStatus.DONE
    return result.reason == STOPPED_BY_A_PERSON and result.status is not StageStatus.DONE
