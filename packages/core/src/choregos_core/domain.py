"""Modèles de domaine partagés par les adaptateurs, l'orchestrateur et l'API.

Ce sont les types qui circulent dans les signatures de `packages/adapters/base.py` (§1.9).
Ils sont sérialisables (pydantic) parce qu'ils traversent les frontières d'activité Temporal.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from choregos_contracts import (
    ExecutorKind,
    MemoryKind,
    ReleaseStatus,
    Risk,
    RunStatus,
    Size,
    StageInput,
    StageResult,
)
from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


def aware(value: datetime | None) -> datetime | None:
    """Rend un horodatage conscient du fuseau : SQLite rend des `datetime` naïfs."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def elapsed_seconds(start: datetime | None, end: datetime | None) -> float:
    """Durée en secondes entre deux horodatages, robuste aux valeurs naïves ou absentes."""
    start_aware, end_aware = aware(start), aware(end)
    if start_aware is None or end_aware is None:
        return 0.0
    return max(0.0, (end_aware - start_aware).total_seconds())


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ───────────────────────────── tracker ─────────────────────────────


class WorkItemData(Model):
    """Représentation canonique d'un ticket, quel que soit le tracker."""

    key: str
    title: str
    body: str = ""
    url: str | None = None
    state: str | None = None
    labels: list[str] = Field(default_factory=list)
    size: Size | None = None
    risk: Risk | None = None
    assignees: list[str] = Field(default_factory=list)
    author: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    closed_at: datetime | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    comments: list[Comment] = Field(default_factory=list)


class Comment(Model):
    id: str | None = None
    author: str = ""
    body: str = ""
    ts: datetime = Field(default_factory=utcnow)
    marker: str | None = None


class NewItem(Model):
    title: str
    body: str
    labels: list[str] = Field(default_factory=list)
    assignees: list[str] = Field(default_factory=list)
    fields: dict[str, Any] = Field(default_factory=dict)
    repo: str | None = None


class TrackerStateMapping(Model):
    """Comment un état du DSL se reflète dans le tracker."""

    status: str | None = None
    label: str | None = None
    remove_labels: list[str] = Field(default_factory=list)


# ───────────────────────────── SCM ─────────────────────────────


class PrRef(Model):
    repo: str
    number: int
    url: str | None = None
    head: str | None = None
    base: str | None = None


class ReviewState(Model):
    reviewer: str
    state: Literal["approved", "changes_requested", "commented", "dismissed"]
    submitted_at: datetime = Field(default_factory=utcnow)
    body: str = ""


class CheckRun(Model):
    name: str
    status: Literal["queued", "in_progress", "completed"] = "completed"
    conclusion: Literal["success", "failure", "neutral", "cancelled", "timed_out", "skipped"] | None = None
    url: str | None = None
    details: str = ""


class PrState(Model):
    ref: PrRef
    title: str = ""
    body: str = ""
    draft: bool = False
    merged: bool = False
    mergeable: bool | None = None
    head_sha: str | None = None
    checks: list[CheckRun] = Field(default_factory=list)
    reviews: list[ReviewState] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)

    def checks_conclusion(self) -> str | None:
        """Agrégat des check-runs : `failure` gagne, puis `pending`, sinon `success`."""
        if not self.checks:
            return None
        if any(c.status != "completed" for c in self.checks):
            return "pending"
        if any(c.conclusion in {"failure", "timed_out", "cancelled"} for c in self.checks):
            return "failure"
        return "success"

    def review_conclusion(self) -> str | None:
        if not self.reviews:
            return None
        latest: dict[str, ReviewState] = {}
        for review in sorted(self.reviews, key=lambda r: r.submitted_at):
            latest[review.reviewer] = review
        states = {r.state for r in latest.values()}
        if "changes_requested" in states:
            return "changes_requested"
        if "approved" in states:
            return "approved"
        return "commented"


class DiffFile(Model):
    path: str
    status: Literal["added", "modified", "removed", "renamed"] = "modified"
    additions: int = 0
    deletions: int = 0
    patch: str | None = None


class DiffSummary(Model):
    base: str | None = None
    head: str | None = None
    files: list[DiffFile] = Field(default_factory=list)

    @property
    def additions(self) -> int:
        return sum(f.additions for f in self.files)

    @property
    def deletions(self) -> int:
        return sum(f.deletions for f in self.files)

    def paths(self) -> list[str]:
        return [f.path for f in self.files]


# ───────────────────────────── CI / CD ─────────────────────────────


class CiStatus(Model):
    state: Literal["pending", "running", "success", "failure", "error", "unknown"] = "unknown"
    runs: list[CheckRun] = Field(default_factory=list)
    url: str | None = None
    sha: str | None = None


class Change(Model):
    """Une modification à promouvoir : une image sur un app."""

    app: str
    image: str | None = None
    tag: str | None = None
    values: dict[str, Any] = Field(default_factory=dict)


class PromotionRef(Model):
    kind: Literal["pr", "commit"] = "pr"
    url: str | None = None
    ref: str | None = None
    merged: bool = False


class Health(Model):
    status: Literal["Healthy", "Progressing", "Degraded", "Suspended", "Missing", "Unknown"] = "Unknown"
    message: str = ""
    revision: str | None = None


class RolloutState(Model):
    phase: Literal["Progressing", "Paused", "Healthy", "Degraded", "Aborted", "Unknown"] = "Unknown"
    current_step: int = 0
    total_steps: int = 0
    canary_weight: int = 0
    message: str = ""


class Window(Model):
    """Fenêtre de synchronisation Argo CD (`kind=allow|deny`)."""

    kind: Literal["allow", "deny"] = "allow"
    schedule: str = "* * * * *"
    duration: str = "1h"
    applications: list[str] = Field(default_factory=list)
    timezone: str = "Europe/Paris"


# ───────────────────────────── exécution ─────────────────────────────


class StageJobSpec(Model):
    """Ce qu'un `Executor` doit lancer pour une étape."""

    run_id: str
    project_slug: str
    namespace: str
    runner_image: str
    api_url: str
    run_token: str
    stage_input: StageInput | None = None
    timeout_minutes: int = 120
    runtime_class: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    cpu: str = "1"
    memory: str = "2Gi"


class ExecRef(Model):
    kind: ExecutorKind
    name: str
    namespace: str | None = None
    url: str | None = None
    run_id: str | None = None


class ExecStatus(Model):
    state: Literal["pending", "running", "succeeded", "failed", "cancelled", "unknown"] = "unknown"
    exit_code: int | None = None
    message: str = ""
    result_url: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None

    @property
    def finished(self) -> bool:
        return self.state in {"succeeded", "failed", "cancelled"}


# ───────────────────────────── gateway ─────────────────────────────


class VirtualKey(Model):
    key: str
    key_id: str
    budget_usd: float
    expires_at: datetime | None = None
    models: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Spend(Model):
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0
    cost_usd: float = 0.0
    requests: int = 0
    models_used: list[str] = Field(default_factory=list)

    def __add__(self, other: Spend) -> Spend:
        return Spend(
            tokens_in=self.tokens_in + other.tokens_in,
            tokens_out=self.tokens_out + other.tokens_out,
            tokens_cached=self.tokens_cached + other.tokens_cached,
            cost_usd=self.cost_usd + other.cost_usd,
            requests=self.requests + other.requests,
            models_used=sorted(set(self.models_used) | set(other.models_used)),
        )


class GatewayModel(Model):
    model_name: str
    litellm_model: str
    provider: str = ""
    input_cost_per_1k: float | None = None
    output_cost_per_1k: float | None = None
    max_input_tokens: int | None = None
    supports_tool_calling: bool = True
    supports_vision: bool = False
    internal_price: bool = False


# ───────────────────────────── mémoire ─────────────────────────────


class Provenance(Model):
    source: str
    ref: str | None = None
    run_id: str | None = None
    work_item_key: str | None = None
    author: str | None = None
    ts: datetime = Field(default_factory=utcnow)


class Fact(Model):
    kind: MemoryKind
    subject: str
    content: str
    external_id: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    provenance: Provenance | None = None
    paths: list[str] = Field(default_factory=list)


class Memory(Model):
    id: str
    kind: MemoryKind
    subject: str
    content: str
    score: float | None = None
    status: Literal["active", "pending", "superseded", "rejected"] = "active"
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    proposed_by: str | None = None


# ───────────────────────────── notifications ─────────────────────────────


class MessageAction(Model):
    id: str
    label: str
    style: Literal["primary", "danger", "default"] = "default"
    value: str = ""


class Message(Model):
    title: str
    body: str = ""
    url: str | None = None
    severity: Literal["info", "warning", "error", "success"] = "info"
    actions: list[MessageAction] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


# ───────────────────────────── enregistrements ─────────────────────────────


class RunRecord(Model):
    """Ce que l'orchestrateur enregistre pour un run (miroir de la table `runs`)."""

    id: str
    work_item_key: str
    project_slug: str
    transition_id: str
    stage_role: str
    attempt: int = 1
    actor: str | None = None
    backend: str | None = None
    model: str | None = None
    executor_kind: ExecutorKind | None = None
    executor_ref: str | None = None
    status: RunStatus = RunStatus.QUEUED
    started_at: datetime | None = None
    ended_at: datetime | None = None
    spend: Spend = Field(default_factory=Spend)
    cost_usd: float = 0.0
    gateway_key_id: str | None = None
    result: StageResult | None = None
    transcript_url: str | None = None
    context_pack_url: str | None = None
    playbook_checksum: str | None = None
    allowed_paths: list[str] = Field(default_factory=list)


class ReleaseItem(Model):
    work_item_key: str
    title: str | None = None
    sha: str
    pr_url: str | None = None
    risk: Risk | None = None
    labels: list[str] = Field(default_factory=list)


class ReleaseRecord(Model):
    id: str
    project_slug: str
    env: str
    batch_no: int
    status: ReleaseStatus = ReleaseStatus.COLLECTING
    items: list[ReleaseItem] = Field(default_factory=list)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    approved_by: str | None = None
    verdict: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None
    promotion_url: str | None = None


class FindingRecord(Model):
    id: str
    project_slug: str
    title: str
    type: str
    severity: str
    evidence: str
    suggested_fix: str | None = None
    estimate: str | None = None
    status: str = "pending"
    origin_work_item_key: str | None = None
    origin_run_id: str | None = None
    created_work_item_key: str | None = None
    duplicate_of: str | None = None
    occurrences: int = 1
    relevant: bool | None = None
    created_at: datetime = Field(default_factory=utcnow)


class LaunchContext(Model):
    """Ce dont un backend a besoin pour écrire sa configuration dans le workspace."""

    workspace: str
    stage_input: StageInput
    agents_md: str | None = None
    playbook_prompt: str = ""
    mcp_servers: dict[str, dict[str, Any]] = Field(default_factory=dict)


class LaunchSpec(Model):
    command: list[str]
    env: dict[str, str] = Field(default_factory=dict)
    files: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
