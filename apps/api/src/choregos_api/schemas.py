"""DTO de l'API : les corps de requête et de réponse décrits par `openapi.yaml`."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from choregos_contracts import ProjectConfig, Role, StageResult
from pydantic import BaseModel, ConfigDict, Field


class Dto(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class PageMeta(Dto):
    next_cursor: str | None = None
    has_more: bool = False


class MembershipDto(Dto):
    user_id: str | None = None
    email: str | None = None
    org: str
    project_slug: str | None = None
    role: Role


class MeDto(Dto):
    id: str
    email: str
    display_name: str
    oidc_sub: str | None = None
    last_login_at: datetime | None = None
    memberships: list[MembershipDto] = Field(default_factory=list)


class MembershipUpsert(Dto):
    email: str
    role: Role
    project_slug: str | None = None


class ProjectStats(Dto):
    active_work_items: int = 0
    cost_month_eur: float = 0.0
    budget_month_eur: float | None = None
    trains_pending: int = 0
    first_pass_merge_rate: float | None = None
    cycle_time_p50_hours: float | None = None


class ProjectDto(Dto):
    id: str
    slug: str
    org: str
    name: str
    template_ref: str | None = None
    status: str
    config: dict[str, Any]
    workflow_name: str | None = None
    policy_name: str | None = None
    stats: ProjectStats = Field(default_factory=ProjectStats)
    created_at: datetime
    updated_at: datetime


class ProjectPage(Dto):
    items: list[ProjectDto]
    meta: PageMeta


class ProjectCreate(Dto):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    name: str
    template_ref: str | None = None
    config: ProjectConfig
    inputs: dict[str, Any] = Field(default_factory=dict)


class ProjectUpdate(Dto):
    name: str | None = None
    config: ProjectConfig | None = None


class ProvisionRequest(Dto):
    inputs: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False
    resume: bool = True


class ProvisionStep(Dto):
    name: str
    status: Literal["pending", "running", "succeeded", "failed", "skipped"] = "pending"
    message: str | None = None
    remediation: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None


class ProvisionStatus(Dto):
    project_id: str
    workflow_id: str | None = None
    status: Literal["pending", "running", "succeeded", "failed"] = "pending"
    current_step: str | None = None
    steps: list[ProvisionStep] = Field(default_factory=list)


class ConnectorDto(Dto):
    id: str | None = None
    kind: str
    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    secret_ref: str | None = None
    status: str = "unknown"
    last_check_at: datetime | None = None
    last_error: str | None = None


class ConnectorUpsert(Dto):
    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    secret_ref: str | None = None


class ConnectorCheck(Dto):
    name: str
    ok: bool
    detail: str | None = None


class ConnectorTestResult(Dto):
    ok: bool
    checks: list[ConnectorCheck] = Field(default_factory=list)


class ConnectorType(Dto):
    kind: str
    type: str
    display: str
    available: bool = True
    config_schema: dict[str, Any] = Field(default_factory=dict)


class WorkflowDefDto(Dto):
    id: str | None = None
    name: str
    version: int
    source: str
    yaml: str
    json_doc: dict[str, Any] | None = Field(default=None, alias="json")
    checksum: str
    is_active: bool = True

    model_config = ConfigDict(extra="forbid", from_attributes=True, populate_by_name=True)


class WorkflowPut(Dto):
    yaml: str
    source: Literal["repo", "platform", "template"] = "platform"
    activate: bool = True


class WorkflowValidateRequest(Dto):
    yaml: str | None = None
    json_doc: dict[str, Any] | None = Field(default=None, alias="json")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class WorkflowIssue(Dto):
    code: str
    message: str
    path: str | None = None
    line: int | None = None
    column: int | None = None


class WorkflowValidation(Dto):
    valid: bool
    errors: list[WorkflowIssue] = Field(default_factory=list)
    warnings: list[WorkflowIssue] = Field(default_factory=list)
    graph: dict[str, Any] | None = None


class WorkflowTemplateDto(Dto):
    name: str
    version: int
    display: str
    description: str = ""
    yaml: str


class PolicyDto(Dto):
    id: str | None = None
    name: str
    version: int
    yaml: str
    json_doc: dict[str, Any] | None = Field(default=None, alias="json")
    is_active: bool = True

    model_config = ConfigDict(extra="forbid", from_attributes=True, populate_by_name=True)


class PolicyPut(Dto):
    yaml: str
    activate: bool = True


class GatewayModelDto(Dto):
    model_name: str
    litellm_model: str
    provider: str = ""
    input_cost_per_1k: float | None = None
    output_cost_per_1k: float | None = None
    max_input_tokens: int | None = None
    supports_tool_calling: bool = True
    supports_vision: bool = False
    internal_price: bool = False


class ProjectModelProfile(Dto):
    litellm_model: str
    params: dict[str, Any] = Field(default_factory=dict)
    max_turns_factor: float = 1.0


class ProjectModels(Dto):
    profiles: dict[str, ProjectModelProfile] = Field(default_factory=dict)
    allow_unvalidated: bool = False
    inherited: dict[str, str] = Field(default_factory=dict)


class ModelMatrixEntry(Dto):
    backend: str
    model: str
    validated: bool
    success_rate: float | None = None
    median_cost_usd: float | None = None
    median_duration_s: float | None = None
    with_memory: bool | None = None
    report_url: str | None = None


class ModelMatrix(Dto):
    generated_at: datetime | None = None
    entries: list[ModelMatrixEntry] = Field(default_factory=list)


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


class ReleaseItemDto(Dto):
    work_item_key: str
    title: str | None = None
    sha: str
    pr_url: str | None = None
    risk: str | None = None
    labels: list[str] = Field(default_factory=list)


class ReleaseDto(Dto):
    id: str
    project_slug: str
    env: str
    batch_no: int
    status: str
    items: list[ReleaseItemDto] = Field(default_factory=list)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    approved_by: str | None = None
    verdict: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None
    promotion_url: str | None = None


class ReleasePage(Dto):
    items: list[ReleaseDto]
    meta: PageMeta


class TrainStatusDto(Dto):
    env: str
    status: str
    batch_size: int = 0
    pending_items: list[str] = Field(default_factory=list)
    next_departure: datetime | None = None
    frozen: bool = False
    freeze_reason: str | None = None
    current_release: ReleaseDto | None = None
    window_open: bool = True


class FreezeRequest(Dto):
    reason: str = Field(min_length=3)


class ApproveRequest(Dto):
    note: str | None = None


class AbortRequest(Dto):
    reason: str | None = None


class FindingDto(Dto):
    id: str
    project_slug: str
    origin_work_item_key: str | None = None
    origin_run_id: str | None = None
    title: str
    type: str
    severity: str
    evidence: str
    suggested_fix: str | None = None
    estimate: str | None = None
    status: str
    created_work_item_key: str | None = None
    duplicate_of: str | None = None
    occurrences: int = 1
    relevant: bool | None = None
    created_at: datetime


class FindingPage(Dto):
    items: list[FindingDto]
    meta: PageMeta


class FindingAction(Dto):
    action: Literal[
        "create_ticket", "mark_duplicate", "dismiss", "agent_ready", "mark_relevant", "mark_false_positive"
    ]
    duplicate_of: str | None = None
    reason: str | None = None


class FindingAck(Dto):
    accepted: bool
    finding_id: str | None = None
    remaining: int = 0


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


class TemplateSummary(Dto):
    name: str
    version: str
    display: str
    description: str | None = None
    repo_url: str | None = None
    is_published: bool = False


class TemplateDetail(TemplateSummary):
    manifest: dict[str, Any] = Field(default_factory=dict)


class TemplateUpsert(Dto):
    manifest: dict[str, Any]
    repo_url: str | None = None
    is_published: bool = False


class ConformanceReport(Dto):
    last_run_at: datetime | None = None
    passed: int | None = None
    total: int | None = None
    report_url: str | None = None
    failures: list[str] = Field(default_factory=list)


class AgentBackendInfo(Dto):
    name: str
    enabled: bool
    version: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    conformance: ConformanceReport = Field(default_factory=ConformanceReport)
    disabled_reason: str | None = None


class AgentBackendUpdate(Dto):
    name: str
    enabled: bool
    disabled_reason: str | None = None


class ExecutorInfo(Dto):
    kind: Literal["tekton", "k8s_job", "local_docker", "aca"]
    enabled: bool = True
    cluster: str | None = None
    namespace_pattern: str | None = None
    runner_image: str | None = None
    default_timeout_minutes: int = 120


class GatewayKeyInfo(Dto):
    key_id: str
    run_id: str | None = None
    project_slug: str | None = None
    budget_usd: float | None = None
    spend_usd: float = 0.0
    expires_at: datetime | None = None
    revoked: bool = False
    created_at: datetime


class AuditEntry(Dto):
    id: str
    actor_id: str | None = None
    actor_kind: str
    action: str
    target_type: str | None = None
    target_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: datetime


class AuditPage(Dto):
    items: list[AuditEntry]
    meta: PageMeta


class WebhookAck(Dto):
    accepted: bool
    duplicate: bool = False
    events: int = 0


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
