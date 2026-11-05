# SPDX-License-Identifier: Apache-2.0
"""Tickets, runs, demandes humaines, diffs, questions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from choregos_contracts import StageResult
from pydantic import ConfigDict, Field

from .base import Dto, PageMeta


class WorkItemCreate(Dto):
    """A request made INSIDE Choregos, when the tracker is internal (`tracker: internal`)."""

    title: str = Field(min_length=1, max_length=500)
    body: str = ""
    size: Literal["S", "M", "L", "XL"] | None = None
    risk: Literal["low", "medium", "high"] | None = None
    #: Start the interpreter right away (the equivalent of `mark_agent_ready`).
    start: bool = True
    #: The workflow where the ticket is born (ADR 0031); otherwise the project's routing, else its default.
    workflow: str | None = None
    #: The ticket's labels, which the routing rules read.
    labels: list[str] = Field(default_factory=list)
    #: The ticket's fields, validated by `metadata.inputs` of its workflow.
    fields: dict[str, Any] = Field(default_factory=dict)
    #: The most this work item may spend, in USD (S27-06, ADR 0047). It may only tighten the
    #: workflow's `budget.ticket_usd` and the policy's ticket budget: wider is refused (422).
    budget_usd: float | None = Field(default=None, gt=0)


class WorkItemUpdate(Dto):
    """The fields that change (S20-05): they replace its own, `null` removes one."""

    model_config = ConfigDict(extra="forbid")

    fields: dict[str, Any]


class Totals(Dto):
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0
    cost_usd: float = 0.0
    cost_eur: float = 0.0
    duration_s: float = 0.0
    runs: int = 0
    #: The model calls the gateway counted (S57-01, ADR 0122): a run's round trips, or a work item's
    #: summed over its runs. Null when unknown: a run booked before S57-01, a remote agent, a gateway
    #: that cannot read a spend back, or a work item one such run belongs to.
    requests: int | None = None
    #: How the cost was priced (ADR 0060): the gateway's figure, the organisation's override, or not at all.
    priced_by: Literal["gateway", "override", "none"] | None = None
    #: The gateway's own figure when an override replaced it.
    gateway_cost_usd: float | None = None
    unmeasured: bool = False


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
    """Why a ticket's interpreter died. The ticket will not move again without a restart."""

    message: str
    activity: str | None = None
    state: str | None = None
    at: datetime | None = None


class WorkItemDto(Dto):
    id: str
    fields: dict[str, Any] = Field(default_factory=dict)
    project_slug: str
    tracker_key: str
    title: str
    body_snapshot: str | None = None
    url: str | None = None
    size: str | None = None
    risk: str | None = None
    state: str
    state_display: str | None = None
    #: How the current state ends the work item when it is terminal (`success`, `declined`,
    #: `cancelled`); `None` while it can still move.
    state_outcome: str | None = None
    workflow_name: str | None = None
    workflow_version: int | None = None
    temporal_wf_id: str | None = None
    #: Temporal status of the workflow (RUNNING, COMPLETED, FAILED...) when it was asked for and
    #: Temporal answered; otherwise nothing. A ticket in a waiting state with a FAILED
    #: workflow is not waiting: it is dead.
    workflow_status: str | None = None
    #: The mark the workflow left when dying. It stays after Temporal has forgotten.
    failure: WorkflowFailure | None = None
    paused: bool = False
    current_run: RunSummary | None = None
    pending_request: HumanRequestDto | None = None
    pr_url: str | None = None
    totals: Totals = Field(default_factory=Totals)
    #: The budget given at launch (or by the workflow), if any; the policy's applies otherwise.
    budget_usd: float | None = None
    estimate: CostEstimate | None = None
    #: Who started the work item (ADR 0077): `person:<email>`, `tracker:<source>`…
    initiated_by: str | None = None
    #: Who filed it, as its tracker names them (#1179): Jira's reporter (`Name <e-mail>`), the author's
    #: login on GitHub or GitLab, the person's e-mail in the console; `None` before 0.21.5.
    reporter: str | None = None
    #: The restricted knowledge bases its runs were served (ADR 0114).
    restricted_knowledge: list[str] = Field(default_factory=list)
    #: A list entry the caller may not read (ADR 0114, point B): only the listed fields are filled.
    redacted: bool = False
    created_at: datetime
    closed_at: datetime | None = None


class WorkItemPage(Dto):
    items: list[WorkItemDto]
    meta: PageMeta


class ReceivedEventDto(Dto):
    """A webhook event the project received, and what the platform did with it (S42-06f)."""

    id: str
    received_at: datetime
    source: str
    event_type: str
    delivery_id: str | None = None
    outcome: str
    work_item_key: str | None = None
    work_item_id: str | None = None
    summary: str


class TriggerDto(Dto):
    """A project's HTTP trigger (S56-01, ADR 0113): what it opens, where it is called, whether it has
    a token. The token itself is shown once, when it is minted."""

    name: str
    url: str
    workflow: str | None = None
    title: str
    key: str | None = None
    labels: list[str] = Field(default_factory=list)
    fields: dict[str, str] = Field(default_factory=dict)
    when: dict[str, Any] = Field(default_factory=dict)
    auth: Literal["token", "signature"] = "token"
    has_token: bool = False
    token_created_at: datetime | None = None


class TriggerTokenDto(Dto):
    """A trigger's new token, shown once: only its SHA-256 is kept."""

    name: str
    url: str
    token: str
    #: The HMAC key of `X-Choregos-Signature` (S56-04); it changes with the token.
    signing_secret: str


class TriggerAck(Dto):
    """What a trigger call did: the outcome `received_events` recorded, or a replayed delivery."""

    accepted: bool
    duplicate: bool = False
    outcome: str | None = None
    work_item_id: str | None = None
    work_item_key: str | None = None
    workflow: str | None = None


class ReceivedEventPage(Dto):
    items: list[ReceivedEventDto]
    meta: PageMeta


class TrackerWrite(Dto):
    """The tracker write behind a timeline entry (ADR 0107 point 4)."""

    type: str
    account: str | None = None


class TimelineEntry(Dto):
    ts: datetime
    kind: str
    title: str
    detail: str | None = None
    actor: str | None = None
    actor_kind: str = "system"
    ref_id: str | None = None
    cost_usd: float | None = None
    via: TrackerWrite | None = None


class HandOffEvent(Dto):
    """A hand-over receipt (S25-04): a run produced or read an output, under this digest."""

    kind: Literal["produced", "read"]
    digest: str
    run_id: str
    stage: str | None = None
    attempt: int | None = None
    at: datetime


class HandOff(Dto):
    """A step output and its receipts, in order; `current_digest` is the digest of what it is
    worth today: the one of the last "produced" receipt, unless it was modified afterwards."""

    output: str
    current_digest: str | None = None
    events: list[HandOffEvent] = Field(default_factory=list)


JourneyMoveKind = Literal[
    "start",
    "nominal",
    "reject",
    "changes_requested",
    "retry",
    "escalate",
    "default",
    "resume",
    "migrated",
    "other",
]


class JourneyMove(Dto):
    """The ticket left `from` for `to`, and the graph edge that led it there (S22-01)."""

    model_config = ConfigDict(populate_by_name=True)

    at: datetime
    from_: str | None = Field(default=None, alias="from")
    to: str
    kind: JourneyMoveKind
    transition_id: str | None = None
    reason: str | None = None


class JourneyStep(Dto):
    """What an actor did for the ticket, on a transition: a run, a request, an action."""

    id: str
    kind: Literal["agent", "human", "action"]
    transition_id: str | None = None
    actor: str | None = None
    role: str | None = None
    attempt: int = Field(default=1, ge=1)
    status: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    summary: str | None = None
    verdict: str | None = None
    cost_usd: float = 0.0
    model: str | None = None
    decided_by: str | None = None
    due_at: datetime | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class WorkItemJourney(Dto):
    """A ticket's journey through ITS workflow, in one read: the map of the version it is
    pinned to, its moves from state to state, and each step of each actor (S22-01)."""

    work_item_id: str
    tracker_key: str | None = None
    workflow_name: str | None = None
    workflow_version: int | None = None
    initial: str | None = None
    state: str
    closed: bool = False
    graph: dict[str, Any]
    process: list[dict[str, Any]] = Field(default_factory=list)
    moves: list[JourneyMove] = Field(default_factory=list)
    steps: list[JourneyStep] = Field(default_factory=list)


class DecisionRequest(Dto):
    request_id: str | None = None
    kind: Literal["approve", "reject", "answer", "scope_change", "complete"]
    answer: str | None = None
    reason: str | None = None
    granted_paths: list[str] = Field(default_factory=list)
    #: `complete`: the values of the task's form, and the attestation (S20-06).
    values: dict[str, Any] = Field(default_factory=dict)
    attested: bool = False


class WorkItemAction(Dto):
    action: Literal["pause", "resume", "stop", "migrate", "rerun_stage", "mark_agent_ready", "reroute"]
    workflow_def_id: str | None = None
    #: `reroute`: the name of the workflow the work item moves to, at its initial state (ADR 0123).
    workflow: str | None = Field(default=None, min_length=1, max_length=100)
    state_mapping: dict[str, str] = Field(default_factory=dict)
    run_id: str | None = None
    reason: str | None = None
    #: `rerun_stage`: money granted on top of the ticket budget (USD), recorded in the audit log.
    #: Required when the work item's budget is spent (S27-04, ADR 0047).
    top_up_usd: float | None = Field(default=None, gt=0)


class ToolConnector(Dto):
    """An organisation connector a run's agent may call, and the account it calls as (ADR 0107)."""

    name: str
    type: str
    auth_scheme: str | None = None
    account: str | None = None


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
    agent_slug: str | None = None
    agent_version: int | None = None
    initiated_by: str | None = None
    tool_connectors: list[ToolConnector] = Field(default_factory=list)


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


class RunInputFile(Dto):
    """A declared input of a run, as the runner mounts it in `.choregos/inputs/` (ADR 0049)."""

    name: str
    version: int
    filename: str
    type: str
    media_type: str
    digest: str
    size: int


class ArtifactRef(Dto):
    url: str
    content_type: str = "application/json"
    size_bytes: int | None = None
    inline: str | None = None


class RunLogsDto(Dto):
    """The runner pod's log tail and how the pod ended (S36-02, S36-03), redacted.

    `source`: `live` while the pod runs (a snapshot refreshed at each orchestrator poll),
    `archive` once the run has ended, `none` when nothing was captured (yet).
    """

    source: Literal["live", "archive", "none"]
    state: str
    exit_code: int | None = None
    reason: str | None = None
    message: str = ""
    lines: list[str] = Field(default_factory=list)
    truncated: bool = False
    error: str | None = None
    captured_at: datetime | None = None


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
    #: The ticket's fields (ADR 0031): the arrival date, the position, what the agent must know.
    fields: dict[str, Any] = Field(default_factory=dict)
    url: str | None = None
    spec_markdown: str | None = None
    plan_markdown: str | None = None
    comments: list[RunTicketComment] = Field(default_factory=list)


class GitToken(Dto):
    """A git token for ONE run: the App's, limited to its repository, one hour (S22-07)."""

    token: str | None = None
    expires_at: datetime | None = None
    repository: str | None = None


class CiLogs(Dto):
    logs: str = ""
    ref: str | None = None
