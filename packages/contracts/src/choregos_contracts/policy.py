# SPDX-License-Identifier: Apache-2.0
"""Policy models (schemas/policy.schema.json)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, Field

from .enums import Risk, Severity, Size
from .stage import (
    MAX_CONCURRENT_TOOL_CALLS,
    Compression,
    HeadroomProfile,
    ShellFilter,
    StageContext,
    ToolScripts,
)
from .workflow import Strict

BySize = dict[str, float]


class Budgets(Strict):
    ticket_usd: dict[str, float] = Field(default_factory=dict)
    stage_usd: dict[str, BySize] = Field(default_factory=dict)
    max_turns: dict[str, int] = Field(default_factory=dict)
    max_minutes: dict[str, int] = Field(default_factory=dict)
    #: Catalogue tool calls allowed per run. `None` = no cap; a paid
    #: catalogue without a cap is an invoice without a cap.
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
    """What a project lets its runs reach (ADR 0070). Egress is always closed: `deny_by_default` is
    kept for old files and read by nothing (`policy_warnings` says so)."""

    allow_domains: list[str] = Field(default_factory=list)
    deny_by_default: bool = True
    #: Hosts the project refuses whatever `allow_domains` says: a deny always wins.
    deny_domains: list[str] = Field(default_factory=list)
    #: New connections a run may open to one host per minute (`None`: the organisation's cap, if any).
    per_host_per_minute: int | None = Field(default=None, ge=1)
    #: New connections a run may open per minute, all hosts together.
    per_run_per_minute: int | None = Field(default=None, ge=1)
    #: The agent CLI's own web tools (`WebFetch`, `WebSearch`): `deny` switches them off.
    builtin_web_tools: Literal["allow", "deny"] = "allow"


class SandboxPolicy(Strict):
    runtime: Literal["default", "gvisor", "kata"] = "default"
    deny_commands: list[str] = Field(
        default_factory=lambda: ["kubectl", "terraform apply", "git push --force", "curl | sh", "sudo"]
    )
    network: NetworkPolicy = Field(default_factory=NetworkPolicy)
    llm_security_analyzer: bool = False
    #: What the runner does with a permission request whose nature it does NOT recognise
    #: (no `kind`, no known verb, no path, no command, no URL): `allow` lets it through,
    #: logging it (the default: the guardrails are a safety net), `reject` refuses it with
    #: the message that names the legitimate tools (closed by default, preset `regulated`).
    unknown_requests: Literal["allow", "reject"] = "allow"
    #: What the orchestrator does when it spots a prompt injection in what the agent is about
    #: to read (ticket, documents, memory): `warn` logs a security event (the default), `block`
    #: stops the stage before any run (the ticket is marked dead with the reason, a human
    #: reviews), `ignore` does not look.
    prompt_injection: Literal["ignore", "warn", "block"] = "warn"
    #: How long a sandbox may stay suspended while a person answers its agent's question, with an
    #: executor that announces `suspend` and `resume` (ADR 0016). Beyond it the sandbox is
    #: destroyed and the stage replays from scratch, as with every other executor. 0: never.
    suspend_max_hours: float = Field(default=24, ge=0)


class WebSearchPolicy(Strict):
    """What a project allows of the built-in `web_search` (ADR 0083). It can only narrow the
    organisation's ceiling, which opens the tool: without a `web_search` section there, it is not
    offered whatever this says."""

    #: `deny` takes the tool away from this project's runs.
    mode: Literal["allow", "deny"] = "allow"
    #: The longest a backend may keep a query (`None`: the ceiling's bound only).
    max_query_retention_days: int | None = Field(default=None, ge=0)
    #: Whether a backend may reuse queries (train, rank, re-index); never wider than the ceiling.
    allow_query_reuse: bool = False


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


def off_from_yaml(value: Any) -> Any:
    """YAML 1.1 (PyYAML) reads an unquoted `off` as `false`: what the author wrote is `off`."""
    return "off" if value is False else value


#: Whether a run's agent may call its tools from a script (ADR 0122).
ToolScriptsMode = Annotated[Literal["off", "offer"], BeforeValidator(off_from_yaml)]


class ToolsOverride(Strict):
    """What a role changes in the project's tool scripts; an absent field keeps the project's value."""

    scripts: ToolScriptsMode | None = None
    max_concurrent_calls: int | None = Field(default=None, ge=1, le=MAX_CONCURRENT_TOOL_CALLS)


class ToolsPolicy(Strict):
    """How a run's agent may call its tools (ADR 0122): `offer` lets it call them from a script too,
    through the runner's tool bridge, under the same governance as the model's calls. Off unless the
    policy offers it; the organisation's ceiling can forbid it, and the organisation's switch wins over
    both (S57-05b: its `tool_scripts` setting, else the deployment's default `CHOREGOS_TOOL_SCRIPTS`, off by
    default, never past the deployment's brake `CHOREGOS_TOOL_SCRIPTS_ALLOWED`)."""

    scripts: ToolScriptsMode = "off"
    #: Calls of one run the bridge relays at once.
    max_concurrent_calls: int = Field(default=4, ge=1, le=MAX_CONCURRENT_TOOL_CALLS)
    #: {stage role: override}, e.g. `{"review": {"scripts": "off"}}` so a reviewer reads every result.
    per_role: dict[str, ToolsOverride] = Field(default_factory=dict)

    def for_role(self, role: str) -> ToolScripts | None:
        """The policy's tool scripts for one role, before the organisation's switch: `None` when off."""
        override = self.per_role.get(role, ToolsOverride())
        if (override.scripts or self.scripts) == "off":
            return None
        return ToolScripts(max_concurrent_calls=override.max_concurrent_calls or self.max_concurrent_calls)


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
    tools: ToolsPolicy = Field(default_factory=ToolsPolicy)
    review: ReviewPolicy = Field(default_factory=ReviewPolicy)
    release_train: dict[str, TrainEnvPolicy] = Field(default_factory=dict)
    web_search: WebSearchPolicy = Field(default_factory=WebSearchPolicy)
