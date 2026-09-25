"""Tickets, runs, demandes humaines, diffs, questions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from choregos_contracts import StageResult
from pydantic import Field

from .base import Dto, PageMeta


class WorkItemCreate(Dto):
    """Une demande posée DANS Choregos, quand le tracker est interne (`tracker: internal`)."""

    title: str = Field(min_length=1, max_length=500)
    body: str = ""
    size: Literal["S", "M", "L", "XL"] | None = None
    risk: Literal["low", "medium", "high"] | None = None
    #: Démarrer l'interpréteur tout de suite (l'équivalent de `mark_agent_ready`).
    start: bool = True


class Totals(Dto):
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0
    cost_usd: float = 0.0
    cost_eur: float = 0.0
    duration_s: float = 0.0
    runs: int = 0


class CostEstimate(Dto):
    median_usd: float | None = None
    p80_usd: float | None = None
    sample_size: int = 0
    over_p80: bool = False


class RunSummary(Dto):
    id: str
    status: str
    stage_role: str
    attempt: int = 1
    backend: str | None = None
    model: str | None = None
    cost_usd: float = 0.0
    started_at: datetime | None = None


class HumanRequestDto(Dto):
    id: str
    work_item_id: str | None = None
    transition_id: str | None = None
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    requested_at: datetime
    due_at: datetime | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision: dict[str, Any] | None = None


class WorkflowFailure(Dto):
    """Pourquoi l'interpréteur d'un ticket est mort. Le ticket ne bougera plus sans redémarrage."""

    message: str
    activity: str | None = None
    state: str | None = None
    at: datetime | None = None


class WorkItemDto(Dto):
    id: str
    project_slug: str
    tracker_key: str
    title: str
    body_snapshot: str | None = None
    url: str | None = None
    size: str | None = None
    risk: str | None = None
    state: str
    state_display: str | None = None
    workflow_name: str | None = None
    workflow_version: int | None = None
    temporal_wf_id: str | None = None
    #: Statut Temporal du workflow (RUNNING, COMPLETED, FAILED…) quand on l'a demandé et que
    #: Temporal a répondu ; sinon rien. Un ticket dans un état d'attente avec un workflow
    #: FAILED n'attend pas : il est mort.
    workflow_status: str | None = None
    #: La marque posée par le workflow en mourant. Reste après que Temporal a oublié.
    failure: WorkflowFailure | None = None
    paused: bool = False
    current_run: RunSummary | None = None
    pending_request: HumanRequestDto | None = None
    pr_url: str | None = None
    totals: Totals = Field(default_factory=Totals)
    estimate: CostEstimate | None = None
    created_at: datetime
    closed_at: datetime | None = None


class WorkItemPage(Dto):
    items: list[WorkItemDto]
    meta: PageMeta


class TimelineEntry(Dto):
    ts: datetime
    kind: str
    title: str
    detail: str | None = None
    actor: str | None = None
    actor_kind: str = "system"
    ref_id: str | None = None
    cost_usd: float | None = None


class DecisionRequest(Dto):
    request_id: str | None = None
    kind: Literal["approve", "reject", "answer", "scope_change"]
    answer: str | None = None
    reason: str | None = None
    granted_paths: list[str] = Field(default_factory=list)


class WorkItemAction(Dto):
    action: Literal["pause", "resume", "stop", "migrate", "rerun_stage", "mark_agent_ready"]
    workflow_def_id: str | None = None
    state_mapping: dict[str, str] = Field(default_factory=dict)
    run_id: str | None = None
    reason: str | None = None


class RunDto(RunSummary):
    work_item_id: str
    project_slug: str
    transition_id: str | None = None
    actor: str | None = None
    executor_kind: str | None = None
    executor_ref: str | None = None
    ended_at: datetime | None = None
    tokens: Totals = Field(default_factory=Totals)
    gateway_key_id: str | None = None
    result: StageResult | None = None
    transcript_url: str | None = None
    context_pack_url: str | None = None
    playbook_checksum: str | None = None
    allowed_paths: list[str] = Field(default_factory=list)


class RunEventDto(Dto):
    seq: int
    type: str
    ts: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class RunEventIn(Dto):
    seq: int = Field(ge=0)
    type: str
    ts: datetime | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class RunEventsBatch(Dto):
    events: list[RunEventIn]


class ArtifactRef(Dto):
    url: str
    content_type: str = "application/json"
    size_bytes: int | None = None
    inline: str | None = None


class DiffFileDto(Dto):
    path: str
    status: str = "modified"
    additions: int = 0
    deletions: int = 0
    patch: str | None = None
    in_scope: bool = True


class DiffSummaryDto(Dto):
    base: str | None = None
    head: str | None = None
    files: list[DiffFileDto] = Field(default_factory=list)
    additions: int = 0
    deletions: int = 0


class ScopeChangeRequestIn(Dto):
    paths: list[str] = Field(min_length=1)
    justification: str


class ScopeChangeDecision(Dto):
    decision: Literal["granted", "pending", "denied"]
    allowed_paths: list[str] = Field(default_factory=list)
    reason: str | None = None


class QuestionIn(Dto):
    text: str
    options: list[str] = Field(default_factory=list)


class RunTicketComment(Dto):
    author: str
    body: str
    ts: datetime


class RunTicket(Dto):
    key: str
    title: str
    body: str = ""
    url: str | None = None
    spec_markdown: str | None = None
    plan_markdown: str | None = None
    comments: list[RunTicketComment] = Field(default_factory=list)


class CiLogs(Dto):
    logs: str = ""
    ref: str | None = None
