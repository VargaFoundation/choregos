"""Modèles de politique (schemas/policy.schema.json)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .enums import Risk, Severity, Size
from .workflow import Strict

BySize = dict[str, float]


class Budgets(Strict):
    ticket_usd: dict[str, float] = Field(default_factory=dict)
    stage_usd: dict[str, BySize] = Field(default_factory=dict)
    max_turns: dict[str, int] = Field(default_factory=dict)
    max_minutes: dict[str, int] = Field(default_factory=dict)
    #: Appels d'outils du catalogue autorisés par run. `None` = aucun plafond ; un
    #: catalogue payant sans plafond, c'est une facture sans plafond.
    tool_calls_per_run: int | None = Field(default=None, ge=0)
    daily_project_usd: float | None = None
    alert_at_ratio: float = Field(default=0.8, ge=0, le=1)


class ApprovalRule(Strict):
    required: Literal["always", "never", "by_size", "by_risk"]
    group: str | None = None
    sizes: list[Size] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    timeout_hours: int | None = Field(default=None, ge=1)


class Approvals(Strict):
    spec: ApprovalRule | None = None
    merge: ApprovalRule | None = None
    prod: ApprovalRule | None = None
    scope_change: ApprovalRule | None = None


class Attempts(Strict):
    default_max: int = Field(default=2, ge=1)
    by_role: dict[str, int] = Field(default_factory=dict)
    dod_iterations: int = Field(default=3, ge=0, le=10)


class ScopePolicy(Strict):
    auto_grant_max_files: int = Field(default=3, ge=0)
    deny_paths: list[str] = Field(
        default_factory=lambda: [".choregos/**", ".github/workflows/**", "charts/**"]
    )
    max_diff_files: int = Field(default=60, ge=1)
    max_diff_lines: int = Field(default=3000, ge=1)


class NetworkPolicy(Strict):
    allow_domains: list[str] = Field(default_factory=list)
    deny_by_default: bool = True


class SandboxPolicy(Strict):
    runtime: Literal["default", "gvisor", "kata"] = "default"
    deny_commands: list[str] = Field(
        default_factory=lambda: ["kubectl", "terraform apply", "git push --force", "curl | sh", "sudo"]
    )
    network: NetworkPolicy = Field(default_factory=NetworkPolicy)
    llm_security_analyzer: bool = False


class FindingsPolicy(Strict):
    max_per_run: int = Field(default=5, ge=0)
    dedupe_threshold: float = Field(default=0.86, ge=0, le=1)
    auto_agent_ready_sizes: list[Size] = Field(default_factory=list)
    notify_severities: list[Severity] = Field(default_factory=lambda: [Severity.CRITICAL])
    false_positive_alert_ratio: float = Field(default=0.3, ge=0, le=1)


class MemoryPolicy(Strict):
    enabled: bool = True
    context_budget_tokens: dict[str, int] = Field(default_factory=dict)
    auto_accept_after_runs: int = Field(default=2, ge=1)
    read_timeout_ms: int = Field(default=300, ge=50)


class ReviewPolicy(Strict):
    cross_backend: bool = False
    require_human_for_risk: list[Risk] = Field(default_factory=lambda: [Risk.HIGH])


class TrainApproval(Strict):
    group: str | None = None
    required: bool = False
    timeout_hours: int = Field(default=4, ge=1)


class CanarySpec(Strict):
    steps: list[int] = Field(default_factory=list)
    analysis: str | None = None
    step_minutes: list[int] = Field(default_factory=list)


class ExpressLane(Strict):
    label: str = "hotfix"
    soak_minutes: int = Field(default=5, ge=0)
    skip_schedule: bool = True
    approval: TrainApproval | None = None


class TerraformSpec(Strict):
    via: Literal["atlantis", "none"] = "none"
    apply_requires_approval: bool = True


class TrainEnvPolicy(Strict):
    mode: Literal["auto_sync", "train"] = "train"
    schedule: str | None = None
    timezone: str = "Europe/Paris"
    windows: list[str] = Field(default_factory=list)
    batch_min: int = Field(default=1, ge=1)
    batch_max: int = Field(default=8, ge=1)
    soak_minutes: int = Field(default=10, ge=0)
    approval: TrainApproval | None = None
    canary: CanarySpec | None = None
    express_lane: ExpressLane | None = None
    freeze: bool = False
    auto_rollback: bool = True
    freeze_on_rollback: bool = True
    terraform: TerraformSpec | None = None


class PolicyMetadata(Strict):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    version: int = Field(ge=1)
    preset: Literal["solo", "team", "regulated"] | None = None
    description: str | None = None


class Policy(Strict):
    apiVersion: Literal["choregos/v1"] = "choregos/v1"  # noqa: N815
    kind: Literal["Policy"] = "Policy"
    metadata: PolicyMetadata
    budgets: Budgets = Field(default_factory=Budgets)
    approvals: Approvals = Field(default_factory=Approvals)
    attempts: Attempts = Field(default_factory=Attempts)
    scope: ScopePolicy = Field(default_factory=ScopePolicy)
    sandbox: SandboxPolicy = Field(default_factory=SandboxPolicy)
    findings: FindingsPolicy = Field(default_factory=FindingsPolicy)
    memory: MemoryPolicy = Field(default_factory=MemoryPolicy)
    review: ReviewPolicy = Field(default_factory=ReviewPolicy)
    release_train: dict[str, TrainEnvPolicy] = Field(default_factory=dict)
