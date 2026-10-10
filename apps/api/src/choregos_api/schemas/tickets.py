# SPDX-License-Identifier: Apache-2.0
"""Tickets, runs, demandes humaines, diffs, questions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from choregos_contracts import StageResult
from pydantic import ConfigDict, Field

from .base import Dto, PageMeta


class WorkItemCreate(Dto):
    """Une demande posée DANS Choregos, quand le tracker est interne (`tracker: internal`)."""

    title: str = Field(min_length=1, max_length=500)
    body: str = ""
    size: Literal["S", "M", "L", "XL"] | None = None
    risk: Literal["low", "medium", "high"] | None = None
    #: Démarrer l'interpréteur tout de suite (l'équivalent de `mark_agent_ready`).
    start: bool = True
    #: Le workflow où le ticket naît (ADR 0031) ; sinon le routage du projet, sinon son défaut.
    workflow: str | None = None
    #: Les étiquettes du ticket, que les règles de routage lisent.
    labels: list[str] = Field(default_factory=list)
    #: Les champs du ticket, validés par `metadata.inputs` de son workflow.
    fields: dict[str, Any] = Field(default_factory=dict)


class WorkItemUpdate(Dto):
    """Les champs qui changent (S20-05) : ils remplacent les siens, `null` en retire un."""

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


class HandOffEvent(Dto):
    """Un reçu de passage (S25-04) : un run a produit ou lu une sortie, sous cette empreinte."""

    kind: Literal["produced", "read"]
    digest: str
    run_id: str
    stage: str | None = None
    attempt: int | None = None
    at: datetime


class HandOff(Dto):
    """Une sortie d'étape et ses reçus, dans l'ordre ; `current_digest` est l'empreinte de ce qu'elle
    vaut aujourd'hui — celle du dernier reçu « produite », sauf si on l'a modifiée après coup."""

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
    """Le ticket a quitté `from` pour `to`, et l'arête du graphe qui l'y a mené (S22-01)."""

    model_config = ConfigDict(populate_by_name=True)

    at: datetime
    from_: str | None = Field(default=None, alias="from")
    to: str
    kind: JourneyMoveKind
    transition_id: str | None = None
    reason: str | None = None


class JourneyStep(Dto):
    """Ce qu'un acteur a fait pour le ticket, sur une transition : un run, une demande, une action."""

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
    """Le parcours d'un ticket dans SON workflow, en une lecture : la carte de la version où il est
    épinglé, ses déplacements d'état en état, et chaque pas de chaque acteur (S22-01)."""

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
    #: `complete` : les valeurs du formulaire de la tâche, et l'attestation (S20-06).
    values: dict[str, Any] = Field(default_factory=dict)
    attested: bool = False


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
    #: Les champs du ticket (ADR 0031) : la date d'arrivée, le poste — ce que l'agent doit savoir.
    fields: dict[str, Any] = Field(default_factory=dict)
    url: str | None = None
    spec_markdown: str | None = None
    plan_markdown: str | None = None
    comments: list[RunTicketComment] = Field(default_factory=list)


class GitToken(Dto):
    """Un jeton git pour UN run : celui de l'App, limité à son dépôt, une heure (S22-07)."""

    token: str | None = None
    expires_at: datetime | None = None
    repository: str | None = None


class CiLogs(Dto):
    logs: str = ""
    ref: str | None = None
