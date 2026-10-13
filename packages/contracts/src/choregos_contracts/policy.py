# SPDX-License-Identifier: Apache-2.0
"""Modèles de politique (schemas/policy.schema.json)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .enums import Risk, Severity, Size
from .stage import Compression, HeadroomProfile, ShellFilter, StageContext
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
    #: Accepted, NOT enforced (S27-08): a transition's attempts are its workflow's `max_attempts`.
    #: `policy_warnings` says so to whoever writes it; the presets no longer do.
    default_max: int = Field(default=2, ge=1)
    #: A ceiling on an agent transition's `max_attempts`, per role: the policy only lowers what the
    #: workflow declares (S27-08, ADR 0047).
    by_role: dict[str, int] = Field(default_factory=dict)
    dod_iterations: int = Field(default=3, ge=0, le=10)
    #: Agent stages a work item may run, retries included; the next is refused and the work item
    #: goes to a person (S27-08, ADR 0047).
    max_stages_per_ticket: int = Field(default=50, ge=1)


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
    #: Ce que le runner fait d'une demande de permission dont il ne reconnaît PAS la nature
    #: (ni `kind`, ni verbe connu, ni chemin, ni commande, ni URL) : `allow` la laisse passer
    #: en la journalisant (le défaut — les garde-fous sont un filet), `reject` la refuse avec
    #: le message qui nomme les outils légitimes (fermé par défaut, preset `regulated`).
    unknown_requests: Literal["allow", "reject"] = "allow"
    #: Ce que l'orchestrateur fait quand il repère une injection de prompt dans ce que
    #: l'agent va lire (ticket, documents, mémoire) : `warn` journalise un événement de
    #: sécurité (le défaut), `block` arrête l'étape avant tout run — le ticket est marqué
    #: mort avec la raison, un humain relit —, `ignore` ne regarde pas.
    prompt_injection: Literal["ignore", "warn", "block"] = "warn"
    #: How long a sandbox may stay suspended while a person answers its agent's question, with an
    #: executor that announces `suspend` and `resume` (ADR 0016). Beyond it the sandbox is
    #: destroyed and the stage replays from scratch, as with every other executor. 0: never.
    suspend_max_hours: float = Field(default=24, ge=0)


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


class ContextOverride(Strict):
    """What a role changes in the project's context reduction; an absent field keeps the
    project's value."""

    shell_filter: ShellFilter | None = None
    compression: Compression | None = None
    headroom_profile: HeadroomProfile | None = None


class ContextPolicy(Strict):
    """Fewer tokens per run (ADR 0055): rtk filters shell output, headroom compresses tool
    results. Everything is off unless the policy turns it on, and the deployment's kill switch
    (`CHOREGOS_CONTEXT_REDUCTION=off`) wins over the policy."""

    shell_filter: ShellFilter = "none"
    compression: Compression = "none"
    headroom_profile: HeadroomProfile = "coding"
    on_unavailable: Literal["bypass", "fail"] = "bypass"
    rtk_exclude_commands: list[str] = Field(default_factory=list)
    #: {stage role: override}, e.g. `{"review": {"compression": "none"}}` so a reviewer reads raw.
    per_role: dict[str, ContextOverride] = Field(default_factory=dict)

    def for_role(self, role: str) -> StageContext:
        """The policy's values for one role, before the deployment's kill switch."""
        override = self.per_role.get(role, ContextOverride())
        return StageContext(
            shell_filter=override.shell_filter or self.shell_filter,
            compression=override.compression or self.compression,
            headroom_profile=override.headroom_profile or self.headroom_profile,
            on_unavailable=self.on_unavailable,
            rtk_exclude_commands=list(self.rtk_exclude_commands),
        )


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
    context: ContextPolicy = Field(default_factory=ContextPolicy)
    review: ReviewPolicy = Field(default_factory=ReviewPolicy)
    release_train: dict[str, TrainEnvPolicy] = Field(default_factory=dict)
