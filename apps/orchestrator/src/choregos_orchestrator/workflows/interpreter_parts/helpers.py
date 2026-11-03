# SPDX-License-Identifier: Apache-2.0
"""Constants, input types and pure functions of `WorkflowInterpreter`.

Moved as they were from `workflows/interpreter.py`, which re-exports them."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

from ...wire_names import both_names

#: The maximum wait of a run IN THE QUEUE (six hours), the same value as
#: `activities.stage.FILE_MAX_MINUTES`: written here because a workflow does not import the
#: activities module, and because it goes into Temporal bounds.
FILE_MAX_MINUTES = 360


DEFAULT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=5,
)
NO_RETRY = RetryPolicy(maximum_attempts=1)
#: For an activity that only POLLS (`await_run`): idempotent, hence replayable. It used to run
#: with no retry, and a worker restart during a run (a `helm upgrade`, bench of 2026-09-24) made it
#: expire on its heartbeat and killed the ticket.
POLL_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=20,
)
HISTORY_THRESHOLD = 20_000
#: Returned by `_wait_external` when a migration wakes it: the state does not move, the loop migrates.
_MIGRATION = "\x00migration"
#: The net of a waiting action: re-read from the database at this pace, in case `action_settled` is lost.
ACTION_REREAD = timedelta(hours=6)
#: The safety net of a join: the children re-read at this pace, in case a `child_settled` is lost.
CHILDREN_REREAD = timedelta(minutes=30)
#: The non-retryable error types `prepare_stage` raises for a configuration an operator can fix.
#: Any other failure — a bug, an unreachable database once its retries are spent, an admission rule,
#: a suspected prompt injection — still fails the interpreter, as before. Three of them were born
#: French: both spellings are read (S45-03), since a history recorded before S45-39 holds the French.
CONFIGURATION_REFUSALS = frozenset(
    {
        "ModelResolutionError",
        "SkillPendingReview",
        "KnowledgeRefused",
        "BackendDisabled",  # S42-07c: the platform administrator switched the backend off
        *both_names("AgentUnavailable", "InvalidInstructions", "SkillMissing"),
    }
)


@dataclass(frozen=True)
class Refusal:
    """A typed refusal an activity raises, and how the work item escalates on it."""

    types: frozenset[str]  # the non-retryable error types that are this refusal
    marker: str  # the `workflow.patched` id behind which it escalates
    reason: str  # the state change's reason
    question: str  # the request's question, on the question state
    kind: str  # the request's `reason`


@dataclass
class InterpreterInput:
    """What is needed to start (or resume) the interpretation of a ticket."""

    project_id: str
    project_slug: str
    work_item_id: str
    tracker_key: str
    resume_from: str | None = None
    attempts: dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0
    history_threshold: int = HISTORY_THRESHOLD
    #: The attempts already counted when the ticket resumed, per transition (see `_counted_attempts`).
    attempts_reset: dict[str, int] = field(default_factory=dict)
    #: Where to resume a parked ticket: `[parked state, origin state, transition]`.
    parked: list[str] | None = None
    #: The run whose sandbox is suspended while a person answers (S35-03), across a
    #: `continue_as_new`: `{run_id, key, until, answered}`.
    suspended: dict[str, Any] | None = None
    #: The outputs of the last stage, which a transition's `when` reads (S48-01), across a
    #: `continue_as_new`.
    last_outputs: dict[str, Any] | None = None


@dataclass
class InterpreterStatus:
    state: str
    cost_usd: float
    attempts: dict[str, int]
    current_run: str | None
    paused: bool
    stopped: bool
    pending_request: str | None = None


async def wait_signal(condition: Any, timeout: timedelta | None = None) -> bool:  # noqa: ASYNC109
    """`workflow.wait_condition` that returns `False` on expiry instead of raising.

    (In the Temporal SDK, a `wait_condition` that expires raises `asyncio.TimeoutError`; here we
    want to tell "the condition arrived" from "the delay elapsed".)
    """
    if timeout is None:
        await workflow.wait_condition(condition)
        return True
    try:
        await workflow.wait_condition(condition, timeout=timeout)
    except TimeoutError:
        return False
    return True


def _decision(state: str, reason: str) -> Any:
    from choregos_core import Decision

    return Decision(next_state=state, escalated=True, reason=reason)


def _for_this_environment(event: dict[str, Any], env: str) -> bool:
    """A deployment event counts for the waiting train if it aims at its environment.
    With no environment named (an event carried by hand, from before ADR 0041), it counts."""
    aimed_at = (event.get("payload") or {}).get("env")
    return aimed_at is None or str(aimed_at) == env
