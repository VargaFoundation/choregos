# SPDX-License-Identifier: Apache-2.0
"""Memory, A/B, costs, DORA, cross review."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from .base import Dto


class MemoryDto(Dto):
    id: str
    kind: str
    subject: str
    content: str
    score: float | None = None
    status: str = "active"
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    proposed_by: str | None = None


class MemoryDecision(Dto):
    id: str
    action: Literal["accept", "reject"]
    reason: str | None = None


class MemoryReimport(Dto):
    sources: list[str] = Field(default_factory=list)


class CrossBackendArm(Dto):
    """The reviews done by the same backend as the implementer, or by another."""

    reviews: int = 0
    caught: int = 0
    catch_rate: float | None = None
    backends: list[str] = Field(default_factory=list)


class CrossBackendReport(Dto):
    """Does multi-backend catch what a single backend lets through? (S13-03)"""

    since: datetime
    cross_backend_required: bool = False
    same_backend: CrossBackendArm = Field(default_factory=CrossBackendArm)
    other_backend: CrossBackendArm = Field(default_factory=CrossBackendArm)
    verdict: str = "not enough data"
    detail: str = ""


class MemoryAbGroup(Dto):
    """One of the two arms of the A/B: the projects with, or without, a context pack."""

    projects: list[str] = Field(default_factory=list)
    tickets: int = 0
    first_pass_merge_rate: float | None = None
    cost_per_ticket_usd: float | None = None


class MemoryAbReport(Dto):
    """Proof before dependency: does memory pay off? (docs/plan/04, decision 4)"""

    org: str
    weeks: int = 4
    since: datetime | None = None
    with_memory: MemoryAbGroup = Field(default_factory=MemoryAbGroup)
    without_memory: MemoryAbGroup = Field(default_factory=MemoryAbGroup)
    verdict: str
    detail: str = ""


class CostRow(Dto):
    key: str
    cost_usd: float = 0.0
    cost_eur: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0
    runs: int = 0


class CostTotal(Dto):
    cost_usd: float = 0.0
    cost_eur: float = 0.0
    budget_usd: float | None = None
    #: The report's tokens, its rows' summed (ADR 0141 §1); `tokens_cached` is part of `tokens_in`.
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0


class CostReport(Dto):
    group_by: str
    currency: str = "EUR"
    rows: list[CostRow] = Field(default_factory=list)
    total: CostTotal = Field(default_factory=CostTotal)


class BudgetAlertDto(Dto):
    """A budget that crossed a threshold (S29-04): what waits in the inbox until someone has seen it."""

    id: str
    scope: str
    subject: str
    period: str
    period_key: str
    #: The share of the budget crossed, in percent: 80, 100.
    threshold: int
    spent_usd: float
    budget_usd: float
    status: str
    created_at: datetime | None = None
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None
    #: Whether the person reading may acknowledge it (project owner or above, S29-04c): the console
    #: offers the gesture only to them. The acknowledge endpoint checks again.
    can_acknowledge: bool = False


class DoraMetric(Dto):
    """One of the four DORA measures, with its tier (`elite`...`low`) and its sample."""

    value: float | None = None
    unit: str
    level: str = "unknown"
    sample: int = 0


class DoraReport(Dto):
    """The four DORA measures, computed on the deployments actually recorded."""

    env: str = "prod"
    since: datetime
    until: datetime
    deployments: int = 0
    deployment_frequency: DoraMetric
    lead_time: DoraMetric
    change_failure_rate: DoraMetric
    time_to_restore: DoraMetric
