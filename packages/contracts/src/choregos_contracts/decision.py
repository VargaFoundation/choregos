# SPDX-License-Identifier: Apache-2.0
"""A decision's record (schemas/decision-record.schema.json, ADR 0120).

A decision model returns a probability with each answer, and a vendor's word that it is calibrated is
not a measure: calibration is a property of a population, and the population that matters is the
organisation's own. Each answered question is therefore recorded with what followed it — the
transition taken, then whether a person contradicted it, how the work item ended, whether it came
back — and calibration is computed from those records, never declared.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from .workflow import DecisionName, Strict

#: Where a recorded probability comes from: a decision step's model, or the `confidence` an agent
#: declared (ADR 0106), recorded so that both are measured on the same footing.
DecisionSource = Literal["decision_model", "agent"]
NonEmpty = Annotated[str, Field(min_length=1)]


class DecisionOutcome(Strict):
    """What was observed after the decision; each field is filled in when it becomes known."""

    #: A later human step on the work item contradicted the decision.
    overridden: bool | None = None
    #: The work item's terminal outcome.
    terminal: Literal["success", "declined", "cancelled"] | None = None
    #: The work item came back after it ended.
    reopened: bool = False
    observed_at: datetime | None = None


class DecisionRecord(Strict):
    """One answered question of a decision."""

    run_id: NonEmpty
    work_item: NonEmpty
    project: NonEmpty
    workflow: NonEmpty
    #: The state the step ran from.
    step: NonEmpty
    question: DecisionName
    source: DecisionSource
    model: NonEmpty
    model_version: str | None = None
    answer: bool | int | float | str
    probability: float = Field(ge=0, le=1)
    #: The state the workflow went to after the step, once known.
    transition: str | None = None
    decided_at: datetime
    outcome: DecisionOutcome | None = None
