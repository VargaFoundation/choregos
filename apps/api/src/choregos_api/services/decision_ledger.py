# SPDX-License-Identifier: Apache-2.0
"""The decision ledger: every answered question, what followed it, and calibration measured from it
(ADR 0121 §6, §7, S52-05).

Written by the orchestrator, where each event already passes — never by a polling loop:

- **the record** — when a run's result is written: the `decide` activity for a decision step's
  answers (`source = decision_model`), `record_run_outcome` for the confidence an agent declared
  (`source = agent`, the rule of `choregos_core.decision_ledger`). Inserted with `ON CONFLICT DO
  NOTHING` on `(run_id, question)`: a retried activity adds nothing.
- **the transition taken** — when the interpreter selects the transition after the step
  (`note_transition_taken`): the state that transition leads to. Filled once (`transition IS NULL`).
- **overridden** — by the first human step decided after the decision (`close_human_request`): an
  approval or a task, on a transition carried by a `human` actor. Sent back or rejected → `true`;
  approved or completed → `false`. Later verdicts do not change it.
- **terminal** — when the work item ends (`close_out`): its terminal state's outcome (`success`,
  `declined`, `cancelled`), and `cancelled` for a work item stopped outside a terminal state.
- **reopened** — when a tracker event says the issue of an ended work item is open again (the webhook
  dispatch): GitHub's `reopened`, GitLab's `reopen`, a Jira status change out of a status a terminal
  state maps to.

Not detected, and said so in `docs/usage.md`: a move by hand in the tracker (the interpreter applies one
only in a state with no way out, where a person is expected to route the work item), a person who edits
the fields or answers a question, an escalation's approval (not a human step of the workflow).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from choregos_core import utcnow
from choregos_core.calibration import DEFAULT_TARGET, MIN_ABOVE, MIN_OBSERVED, calibrate
from choregos_core.decision_ledger import Answered, is_right
from choregos_core.domain import aware
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..db.base import uuid7
from ..db.models import DecisionRecordRow
from ..schemas.definitions import QuestionCalibration, ReliabilityBin, StepCalibration, ThresholdPoint
from .definitions import workflow_du_ticket, workflow_model

DECISION_MODEL = "decision_model"
AGENT = "agent"
#: The outcome of a work item stopped outside a terminal state: dropped, as the A2A door says.
CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class DecidedBy:
    """Who answered, where, and when: what every record of one run shares."""

    org_id: str
    project_id: str
    project_slug: str
    work_item_id: str
    work_item_key: str
    run_id: str
    workflow: str
    workflow_version: int | None
    #: The state the step ran from, and its transition.
    step: str
    transition_id: str | None
    source: str
    model: str
    model_version: str | None
    decided_at: datetime


async def record_decisions(session: Any, decided: DecidedBy, answers: Sequence[Answered]) -> int:
    """One row per answered question; a question the run already recorded is left as it is.

    Returns how many rows were added (0 on a replay)."""
    if not answers:
        return 0
    rows = [
        {
            "id": uuid7(),
            "org_id": decided.org_id,
            "project_id": decided.project_id,
            "work_item_id": decided.work_item_id,
            "run_id": decided.run_id,
            "work_item_key": decided.work_item_key[:255],
            "project_slug": decided.project_slug[:64],
            "workflow": decided.workflow[:100],
            "workflow_version": decided.workflow_version,
            "step": decided.step[:64],
            "transition_id": decided.transition_id[:64] if decided.transition_id else None,
            "question": answer.question,
            "source": decided.source,
            "model": decided.model[:200],
            "model_version": decided.model_version[:200] if decided.model_version else None,
            "answer": answer.answer,
            "probability": answer.probability,
            "decided_at": decided.decided_at,
            "reopened": False,
        }
        for answer in answers
    ]
    await session.flush()  # the run the rows point to is written first
    statement: Any
    if session.get_bind().dialect.name == "postgresql":
        statement = postgresql_insert(DecisionRecordRow).values(rows)
    else:
        statement = sqlite_insert(DecisionRecordRow).values(rows)
    result = await session.execute(statement.on_conflict_do_nothing(index_elements=["run_id", "question"]))
    return int(getattr(result, "rowcount", 0) or 0)


async def record_transition_taken(session: Any, run_id: str, state: str) -> int:
    """The state the workflow went to after the step: written once, on the run's records."""
    result = await session.execute(
        update(DecisionRecordRow)
        .where(DecisionRecordRow.run_id == run_id, DecisionRecordRow.transition.is_(None))
        .values(transition=state[:64])
    )
    return int(result.rowcount or 0)


async def record_human_verdict(
    session: Any, work_item_id: str, requested_at: datetime | None, approved: bool
) -> int:
    """A human step's verdict on the decisions made before it was asked, not judged yet.

    Rejected (sent back) → overridden; approved → not overridden. Only the first verdict counts."""
    asked = aware(requested_at) or utcnow()
    pending = (
        (
            await session.execute(
                select(DecisionRecordRow).where(
                    DecisionRecordRow.work_item_id == work_item_id, DecisionRecordRow.overridden.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    now = utcnow()
    judged = 0
    for row in pending:
        decided = aware(row.decided_at)
        if decided is not None and decided <= asked:
            row.overridden = not approved
            row.observed_at = now
            judged += 1
    return judged


async def record_terminal_outcome(session: Any, work_item_id: str, outcome: str) -> int:
    """How the work item ended, on every record of it that does not know yet."""
    result = await session.execute(
        update(DecisionRecordRow)
        .where(DecisionRecordRow.work_item_id == work_item_id, DecisionRecordRow.terminal.is_(None))
        .values(terminal=outcome, observed_at=utcnow())
    )
    return int(result.rowcount or 0)


async def record_reopened(session: Any, work_item_id: str) -> int:
    """The work item came back after it ended: its success was not one."""
    result = await session.execute(
        update(DecisionRecordRow)
        .where(DecisionRecordRow.work_item_id == work_item_id, DecisionRecordRow.reopened.is_(False))
        .values(reopened=True, observed_at=utcnow())
    )
    return int(result.rowcount or 0)


def tracker_says_reopened(event_type: str, payload: dict[str, Any], terminal_statuses: Iterable[str]) -> bool:
    """Whether a tracker event says an ended work item's issue is open again.

    GitHub's `reopened` and GitLab's `reopen` arrive as `tracker.item.updated` marked `reopened`; a
    Jira status change (`tracker.item.moved`) reopens when it leaves a status a terminal state of the
    workflow maps to for one no terminal state maps to. Status names compare case-blind."""
    if event_type == "tracker.item.updated":
        return payload.get("reopened") is True
    if event_type != "tracker.item.moved":
        return False
    terminal = {str(status).strip().casefold() for status in terminal_statuses if status}
    left = str(payload.get("from_status") or "").strip().casefold()
    entered = str(payload.get("to_status") or "").strip().casefold()
    return bool(left) and left in terminal and bool(entered) and entered not in terminal


async def observe_reopening(session: Any, item: Any, event_type: str, payload: dict[str, Any]) -> int:
    """A tracker event on an ended work item (`closed_at` set) that says its issue is open again marks
    its decisions reopened (`tracker_says_reopened`). Any other event, or a work item still running,
    changes nothing."""
    if item.closed_at is None or event_type not in ("tracker.item.updated", "tracker.item.moved"):
        return 0
    workflow = workflow_model(await workflow_du_ticket(session, item))
    statuses = [
        state.tracker.status for state in workflow.states.values() if state.terminal and state.tracker
    ]
    if not tracker_says_reopened(event_type, payload, [status for status in statuses if status]):
        return 0
    return await record_reopened(session, item.id)


async def step_calibration(
    session: Any, project_id: str, workflow: str, step: str, target: float = DEFAULT_TARGET
) -> StepCalibration:
    """A step's decisions measured from the ledger, per question, source, model and model version."""
    rows = (
        (
            await session.execute(
                select(DecisionRecordRow)
                .where(
                    DecisionRecordRow.project_id == project_id,
                    DecisionRecordRow.workflow == workflow,
                    DecisionRecordRow.step == step,
                )
                .order_by(DecisionRecordRow.decided_at)
            )
        )
        .scalars()
        .all()
    )
    groups: dict[tuple[str, str, str, str | None], list[tuple[float, bool | None]]] = {}
    for row in rows:
        key = (row.question, row.source, row.model, row.model_version)
        groups.setdefault(key, []).append(
            (float(row.probability), is_right(row.overridden, row.terminal, bool(row.reopened)))
        )
    questions = []
    for (question, source, model, model_version), decisions in sorted(
        groups.items(), key=lambda item: (item[0][0], item[0][1], item[0][2], item[0][3] or "")
    ):
        measured = calibrate(decisions, target, min_observed=MIN_OBSERVED, min_above=MIN_ABOVE)
        questions.append(
            QuestionCalibration(
                question=question,
                source=AGENT if source == AGENT else DECISION_MODEL,
                model=model,
                model_version=model_version,
                count=measured.count,
                observed=measured.observed,
                accuracy=measured.accuracy,
                ece=measured.ece,
                bins=[
                    ReliabilityBin(
                        lower=b.lower,
                        upper=b.upper,
                        count=b.count,
                        mean_probability=b.mean_probability,
                        accuracy=b.accuracy,
                    )
                    for b in measured.bins
                ],
                thresholds=[
                    ThresholdPoint(
                        threshold=p.threshold, count=p.count, coverage=p.coverage, accuracy=p.accuracy
                    )
                    for p in measured.thresholds
                ],
                suggested_threshold=measured.suggested_threshold,
                suggestion_reason=measured.suggestion_reason,
            )
        )
    return StepCalibration(
        workflow=workflow,
        step=step,
        target_accuracy=target,
        min_observed=MIN_OBSERVED,
        questions=questions,
    )


__all__ = [
    "AGENT",
    "CANCELLED",
    "DECISION_MODEL",
    "DecidedBy",
    "observe_reopening",
    "record_decisions",
    "record_human_verdict",
    "record_reopened",
    "record_terminal_outcome",
    "record_transition_taken",
    "step_calibration",
    "tracker_says_reopened",
]
