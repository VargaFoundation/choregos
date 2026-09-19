"""StageInput / StageResult : le contrat entre l'orchestrateur, le runner et l'agent."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from .enums import ApiFormat, FindingType, Severity, StageStatus
from .workflow import Strict

STAGE_INPUT_SCHEMA = "choregos/StageInput/v1"
STAGE_RESULT_SCHEMA = "choregos/StageResult/v1"
CONTEXT_PACK_SCHEMA = "choregos/ContextPack/v1"


class ProjectRef(Strict):
    slug: str
    org: str
    test_command: str | None = None
    lint_command: str | None = None
    typecheck_command: str | None = None


class WorkItemLinks(Strict):
    spec_comment: str | None = None
    plan_comment: str | None = None
    pr: str | None = None


class WorkItemRef(Strict):
    key: str
    title: str
    body: str = ""
    url: str | None = None
    size: Literal["S", "M", "L", "XL"] | None = None
    risk: Literal["low", "medium", "high"] | None = None
    links: WorkItemLinks = Field(default_factory=WorkItemLinks)


class TransitionRef(Strict):
    id: str
    role: str
    from_: str = Field(alias="from")
    to: str
    outputs: list[str] = Field(default_factory=list)
    inputs: list[str] = Field(default_factory=list)

    model_config = Strict.model_config | {"populate_by_name": True}


class RepoRef(Strict):
    url: str
    base_branch: str
    work_branch: str
    clone_depth: int = Field(default=50, ge=1)


class LaunchSpec(Strict):
    command: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    files: dict[str, str] = Field(default_factory=dict)


class AgentRef(Strict):
    backend: str
    launch: LaunchSpec = Field(default_factory=LaunchSpec)


class ModelRef(Strict):
    litellm_model: str
    base_url: str
    api_format: ApiFormat = ApiFormat.OPENAI
    params: dict[str, Any] = Field(default_factory=dict)
    provider_model: str | None = None
    """Modèle réel derrière l'alias de plateforme, quand le gateway le connaît.

    L'agent appelle `litellm_model` — c'est l'alias qui porte le routage et le budget. Mais un
    backend contraint (claude-code n'accepte que des modèles Claude) doit vérifier sa contrainte
    sur le **vrai** modèle : sans ce champ, `platform/standard` lui est refusé alors que le
    resolver l'a validé, et la combinaison alias + backend contraint devient impossible.
    """


class Budget(Strict):
    usd: float = Field(gt=0)
    max_turns: int = Field(ge=1)
    max_minutes: int = Field(ge=1)


class PlaybookRef(Strict):
    ref: str
    prompt_url: str | None = None
    prompt: str | None = None


class McpServerRef(Strict):
    url: str | None = None
    command: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class ToolsRef(Strict):
    mcp: dict[str, McpServerRef] = Field(default_factory=dict)


class Permissions(Strict):
    write_paths: list[str] = Field(default_factory=list)
    deny_commands: list[str] = Field(default_factory=list)
    allow_domains: list[str] = Field(default_factory=list)
    dod_iterations: int = Field(default=3, ge=0)
    max_findings: int = Field(default=5, ge=0)


class Callbacks(Strict):
    api_url: str
    run_token: str


class StageInput(Strict):
    """Ce que l'orchestrateur remet au runner (docs/plan/01 §1.5)."""

    schema_: Literal["choregos/StageInput/v1"] = Field(default="choregos/StageInput/v1", alias="schema")
    run_id: str
    attempt: int = Field(ge=1)
    trace_parent: str | None = None
    project: ProjectRef
    work_item: WorkItemRef
    transition: TransitionRef
    repo: RepoRef
    agent: AgentRef
    model: ModelRef
    gateway_key: str | None = None
    budget: Budget
    allowed_paths: list[str] = Field(default_factory=list)
    context_pack_url: str | None = None
    playbook: PlaybookRef
    tools: ToolsRef = Field(default_factory=ToolsRef)
    permissions: Permissions = Field(default_factory=Permissions)
    callbacks: Callbacks

    model_config = Strict.model_config | {"populate_by_name": True}


class Finding(Strict):
    title: str = Field(min_length=3, max_length=200)
    type: FindingType
    severity: Severity
    evidence: str = Field(min_length=1)
    suggested_fix: str | None = None
    estimate: Literal["S", "M", "L"] | None = None
    out_of_scope_reason: str | None = None


class Question(Strict):
    text: str = Field(min_length=1)
    options: list[str] = Field(default_factory=list)


class ScopeChangeRequest(Strict):
    paths: list[str] = Field(min_length=1)
    justification: str = Field(min_length=1)


class Artifacts(Strict):
    branch: str | None = None
    commits: list[str] = Field(default_factory=list)
    pr_url: str | None = None
    transcript_url: str | None = None
    context_pack_url: str | None = None
    reports: dict[str, str] = Field(default_factory=dict)


class Evidence(Strict):
    tests_passed: bool | None = None
    tests_run: int | None = None
    tests_failed: int | None = None
    coverage_delta: float | None = None
    lint: Literal["ok", "failed", "skipped"] | None = None
    typecheck: Literal["ok", "failed", "skipped"] | None = None
    security_scan: Literal["ok", "failed", "skipped"] | None = None
    diff_files: int | None = None
    diff_lines: int | None = None

    def is_complete(self) -> bool:
        """Preuves minimales attendues par la gate `evidence_present`."""
        return self.tests_passed is not None and self.tests_run is not None


class Diagnostics(Strict):
    turns: int = 0
    tool_calls: int = 0
    permission_denials: int = 0
    duration_s: float = 0.0
    agent_exit: Literal["normal", "cancelled", "crashed", "timeout", "limit"] = "normal"
    dod_iterations: int = 0
    result_repairs: int = 0


class StageOutputs(Strict):
    """Sorties structurées d'une étape ; champs supplémentaires tolérés (rôles custom)."""

    model_config = Strict.model_config | {"extra": "allow"}

    size: Literal["S", "M", "L", "XL"] | None = None
    risk: Literal["low", "medium", "high"] | None = None
    allowed_paths: list[str] | None = None
    spec_markdown: str | None = None
    plan_markdown: str | None = None
    review_markdown: str | None = None
    verdict: Literal["approve", "request_changes", "comment"] | None = None
    release_notes_markdown: str | None = None
    duplicate_of: str | None = None
    questions_asked: int | None = None


class StageResult(Strict):
    """Ce que le runner rend à l'orchestrateur (docs/plan/01 §1.6)."""

    schema_: Literal["choregos/StageResult/v1"] = Field(default="choregos/StageResult/v1", alias="schema")
    status: StageStatus
    reason: str | None = None
    summary: str = Field(min_length=1, max_length=4000)
    outputs: StageOutputs = Field(default_factory=StageOutputs)
    artifacts: Artifacts = Field(default_factory=Artifacts)
    evidence: Evidence = Field(default_factory=Evidence)
    questions: list[Question] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    scope_changes_requested: list[ScopeChangeRequest] = Field(default_factory=list)
    diagnostics: Diagnostics = Field(default_factory=Diagnostics)

    model_config = Strict.model_config | {"populate_by_name": True}

    @classmethod
    def failure(cls, summary: str, reason: str) -> StageResult:
        return cls(status=StageStatus.FAILED, summary=summary, reason=reason)

    @property
    def ok(self) -> bool:
        return self.status is StageStatus.DONE


class MemoryItem(Strict):
    id: str | None = None
    kind: str
    subject: str
    content: str
    score: float | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class RelatedItem(Strict):
    key: str
    title: str
    state: str | None = None
    url: str | None = None
    summary: str | None = None


class ContextPack(Strict):
    """Mémoire injectée dans un stage. Contenu **non fiable** : données, jamais instructions."""

    schema_: Literal["choregos/ContextPack/v1"] = Field(default="choregos/ContextPack/v1", alias="schema")
    query: str
    paths: list[str] = Field(default_factory=list)
    kinds: list[str] = Field(default_factory=list)
    budget_tokens: int = 0
    tokens_estimated: int = 0
    truncated: bool = False
    memories: list[MemoryItem] = Field(default_factory=list)
    incidents: list[MemoryItem] = Field(default_factory=list)
    related_items: list[RelatedItem] = Field(default_factory=list)
    generated_at: str | None = None

    model_config = Strict.model_config | {"populate_by_name": True}

    @classmethod
    def empty(cls, query: str = "") -> ContextPack:
        return cls(query=query)

    def is_empty(self) -> bool:
        return not (self.memories or self.incidents or self.related_items)
