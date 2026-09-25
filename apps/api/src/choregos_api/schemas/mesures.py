"""Mémoire, A/B, coûts, DORA, revue croisée."""

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
    """Les revues faites par le même backend que l'implémenteur, ou par un autre."""

    reviews: int = 0
    caught: int = 0
    catch_rate: float | None = None
    backends: list[str] = Field(default_factory=list)


class CrossBackendReport(Dto):
    """Le multi-backend attrape-t-il ce qu'un seul backend laisse passer ? (S13-03)"""

    since: datetime
    cross_backend_required: bool = False
    same_backend: CrossBackendArm = Field(default_factory=CrossBackendArm)
    other_backend: CrossBackendArm = Field(default_factory=CrossBackendArm)
    verdict: str = "échantillon insuffisant"
    detail: str = ""


class MemoryAbGroup(Dto):
    """Un des deux bras de l'A/B : les projets avec, ou sans, context pack."""

    projects: list[str] = Field(default_factory=list)
    tickets: int = 0
    first_pass_merge_rate: float | None = None
    cost_per_ticket_usd: float | None = None


class MemoryAbReport(Dto):
    """Preuve avant dépendance : la mémoire paie-t-elle ? (docs/plan/04, décision 4)"""

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


class CostReport(Dto):
    group_by: str
    currency: str = "EUR"
    rows: list[CostRow] = Field(default_factory=list)
    total: CostTotal = Field(default_factory=CostTotal)


class DoraMetric(Dto):
    """Une des quatre mesures DORA, avec le palier (`elite`…`low`) et son échantillon."""

    value: float | None = None
    unit: str
    level: str = "unknown"
    sample: int = 0


class DoraReport(Dto):
    """Les quatre mesures DORA, calculées sur les déploiements réellement enregistrés."""

    env: str = "prod"
    since: datetime
    until: datetime
    deployments: int = 0
    deployment_frequency: DoraMetric
    lead_time: DoraMetric
    change_failure_rate: DoraMetric
    time_to_restore: DoraMetric
