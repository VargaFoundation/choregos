# SPDX-License-Identifier: Apache-2.0
"""StageInput / StageResult: the contract between the orchestrator, the runner and the agent."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .artifact import ARTIFACT_TYPES
from .enums import ApiFormat, FindingType, Severity, StageStatus
from .workflow import Strict

STAGE_INPUT_SCHEMA = "choregos/StageInput/v1"
STAGE_RESULT_SCHEMA = "choregos/StageResult/v1"
CONTEXT_PACK_SCHEMA = "choregos/ContextPack/v1"


def file_outputs(outputs: list[str], output_types: dict[str, str]) -> list[str]:
    """The declared outputs whose type is a file (ADR 0049); an unknown type is not one."""
    return [
        name
        for name in outputs
        if (entry := ARTIFACT_TYPES.get(output_types.get(name, ""))) is not None and not entry.inline
    ]


def missing_outputs(
    outputs: list[str], output_types: dict[str, str], result: StageResult | None
) -> list[str]:
    """The declared outputs a result does not carry: an inline output absent or empty in
    `outputs`, a file output absent from `artifacts.files`. Whatever their type (S31-02)."""
    if result is None:
        return list(outputs)
    files = set(file_outputs(outputs, output_types))
    written = result.outputs.model_dump(exclude_none=True)
    stored = set(result.artifacts.files)
    return [
        name
        for name in outputs
        if (name not in stored if name in files else written.get(name) in (None, "", [], {}))
    ]


class ProjectRef(Strict):
    slug: str
    org: str
    test_command: str | None = None
    lint_command: str | None = None
    typecheck_command: str | None = None
    #: {fact name: command}. Exit code 0 = true. The runner executes them and the MEASURED fact
    #: overwrites the one the agent declared: this is what separates a guarantee from a story.
    fact_commands: dict[str, str] = Field(default_factory=dict)


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
    #: The declared type of each output (ADR 0049); a file type is written in `.choregos/outputs/`.
    output_types: dict[str, str] = Field(default_factory=dict)

    model_config = Strict.model_config | {"populate_by_name": True}

    def file_outputs(self) -> list[str]:
        """The declared outputs that are files, not values of `outputs`."""
        return file_outputs(self.outputs, self.output_types)


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
    image: str | None = Field(default=None, min_length=1)
    """The image of the run's pod when an agent written in code names its own (ADR 0063): the
    reference the operator mapped the agent's image name to, never a name a workflow wrote."""


class ModelRef(Strict):
    litellm_model: str
    base_url: str
    api_format: ApiFormat = ApiFormat.OPENAI
    params: dict[str, Any] = Field(default_factory=dict)
    provider_model: str | None = None
    """The real model behind the platform alias, when the gateway knows it.

    The agent calls `litellm_model`: the alias carries the routing and the budget. But a
    constrained backend (claude-code accepts only Claude models) must check its constraint on
    the **real** model: without this field, `platform/standard` is refused although the
    resolver validated it, and the combination of alias + constrained backend becomes impossible.
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


#: The most calls of one run a script may have in flight at once through the tool bridge (ADR 0122 §7).
MAX_CONCURRENT_TOOL_CALLS = 16


class ToolScripts(Strict):
    """The run's agent may call the run's tools from a script, through the runner's tool bridge
    (ADR 0122), resolved by the orchestrator from the deployment, the policy and the stage's role.
    Present means offered; off is never sent: a runner older than ADR 0122 refuses the key."""

    #: Calls of this run the bridge relays at once; the next waits.
    max_concurrent_calls: int = Field(default=4, ge=1, le=MAX_CONCURRENT_TOOL_CALLS)


class ToolsRef(Strict):
    mcp: dict[str, McpServerRef] = Field(default_factory=dict)
    #: The tool bridge, when the run is offered it (ADR 0122 §6); `None`: the model's MCP tools only.
    scripts: ToolScripts | None = None


class Permissions(Strict):
    write_paths: list[str] = Field(default_factory=list)
    deny_commands: list[str] = Field(default_factory=list)
    allow_domains: list[str] = Field(default_factory=list)
    #: Hosts the run must never reach: a deny always wins over `allow_domains` (ADR 0070).
    deny_domains: list[str] = Field(default_factory=list)
    #: `unlisted`: an empty `allow_domains` means "no list, the sandbox filters" (old behaviour);
    #: `listed`: the list is complete, an empty one means no internet at all.
    egress: Literal["unlisted", "listed"] = "unlisted"
    #: Whether the agent CLI's own `WebFetch` / `WebSearch` may be used.
    builtin_web_tools: bool = True
    dod_iterations: int = Field(default=3, ge=0)
    max_findings: int = Field(default=5, ge=0)
    unknown_requests: Literal["allow", "reject"] = "allow"


ShellFilter = Literal["none", "rtk"]
Compression = Literal["none", "headroom"]
HeadroomProfile = Literal["coding", "balanced", "general"]


class StageContext(Strict):
    """The context reduction a run starts with, resolved by the orchestrator from the deployment,
    the policy and the stage's role (ADR 0055). The defaults are everything off: a runner that gets
    no `context` reduces nothing."""

    shell_filter: ShellFilter = "none"
    compression: Compression = "none"
    headroom_profile: HeadroomProfile = "coding"
    #: What the runner does when the compression proxy does not start: `bypass` talks to the
    #: gateway directly and journals it, `fail` fails the stage.
    on_unavailable: Literal["bypass", "fail"] = "bypass"
    #: Commands whose output the shell filter leaves raw.
    rtk_exclude_commands: list[str] = Field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.shell_filter != "none" or self.compression != "none"


class Callbacks(Strict):
    api_url: str
    run_token: str


class SkillRef(Strict):
    """A skill the agent carries (ADR 0033): its name, its version, its digest. The files come
    from `GET /internal/runs/{id}/skills`, and the runner checks the digest before laying them
    down: a wrong digest stops the run."""

    slug: str
    version: int = Field(ge=1)
    digest: str


def skill_digest(files: dict[str, str]) -> str:
    """The digest of a skill version: the API computes it at publication, the runner
    recomputes it before laying the files down. One computation for both sides."""
    import hashlib

    from .skill_files import skill_file_bytes

    hasher = hashlib.sha256()
    for path in sorted(files):
        # The bytes of each file (decoded base64 for an inert binary): text digests are unchanged.
        hasher.update(path.encode("utf-8") + b"\0" + skill_file_bytes(path, files[path]) + b"\0")
    return "sha256:" + hasher.hexdigest()


class StageMemory(Strict):
    """One memory a run reads and/or writes, resolved by the platform (S40-11, ADR 0075)."""

    #: `global:<slug>` or `project:<slug>` (`project:shared`, `project:own` for the implicit ones).
    label: str
    scope: Literal["global", "project"]
    slug: str
    mode: Literal["read", "write"] = "read"
    #: The string the memory adapters take to say where (`choregos_core.memory_scope`).
    namespace: str


class KnowledgeRef(Strict):
    """A knowledge base served to the run (ADR 0051).

    `files`: the runner fetches exactly the `documents` named here from
    `GET /internal/runs/{id}/knowledge`, checks their `digest`, and lays them read-only under
    `.choregos/knowledge/<slug>/`. `index`: nothing is laid down; the agent searches the base with
    the sidecar's `knowledge_search` tool.
    """

    slug: str
    name: str = ""
    mode: Literal["files", "index"] = "files"
    #: The converted documents pinned when the run was prepared (`files` mode).
    documents: list[str] = Field(default_factory=list)
    digest: str = ""


def knowledge_digest(files: list[dict[str, str]]) -> str:
    """The digest of a knowledge base as laid down: each file's path and markdown, in order. The API
    computes it when the run is prepared, the runner again before writing."""
    import hashlib

    condense = hashlib.sha256()
    for entry in files:
        condense.update(entry["path"].encode("utf-8") + b"\0" + entry["markdown"].encode("utf-8") + b"\0")
    return "sha256:" + condense.hexdigest()


class JobSpec(Strict):
    """A deterministic step (ADR 0056): the runner runs `command`, starts no agent, holds no model key."""

    command: list[str] = Field(min_length=1)
    #: The image the orchestrator resolved from the actor's image name, `None` for the runner image.
    image: str | None = None
    #: `{connector: [operation patterns]}` the internal API lets this run call (the actor's `tools`).
    tools: dict[str, list[str]] = Field(default_factory=dict)


class DeclaredCost(Strict):
    """What an external agent costs, as its registry entry DECLARES it (ADR 0062): the protocol
    reports no figure. Booked in the ledger as `external`, never as a measurement."""

    per_task_usd: float | None = Field(default=None, ge=0)
    per_minute_usd: float | None = Field(default=None, ge=0)


class RemoteCall(Strict):
    """A step run by a remote agent over A2A (ADR 0062): the runner calls `endpoint` from the pod.

    `agent` is a placeholder and no gateway key exists: the remote agent brings its own model. The
    access token the runner presents is NOT here: it travels in the run's Secret
    (`CHOREGOS_RUN_REMOTE_TOKEN`), like the run key (#523)."""

    protocol: Literal["a2a"] = "a2a"
    #: The JSON-RPC endpoint messages are posted to (the card's `url`, pinned at publication).
    endpoint: str = Field(min_length=1)
    #: Where the card lives; the runner fetches it again and refuses on drift.
    card_url: str = Field(min_length=1)
    protocol_version: str = Field(min_length=1)
    #: The skill the message asks for, when the card has several.
    skill: str | None = None
    #: `sha256:<hex>` of the card as it was when the version was published.
    card_digest: str = Field(min_length=1)
    timeout_s: int = Field(ge=1)
    #: `text`: instructions and task as one text part. `text+files`: the declared inputs too.
    input: Literal["text", "text+files"] = "text"
    declared_cost: DeclaredCost = Field(default_factory=DeclaredCost)
    #: The registry agent's slug, for provenance (`remote:<slug>`) and the ledger.
    agent_slug: str = Field(min_length=1)
    #: The NAME of the organisation credential the access token comes from (never the token). The
    #: orchestrator reads it to mint the token; the runner does not need it.
    credential: str | None = None


#: The runner's loopback listeners a hosted container is pointed at: model, MCP, egress (ADR 0080).
LOOPBACK_PORTS = frozenset({4001, 4002, 4003})


class HostedCall(Strict):
    """A step a hosted agent carries (ADR 0080): the agent's own container, beside the runner in the
    pod, serves Foundry's contract on `port`; the runner sends the step to it and reads the answer.

    `agent.backend` is `hosted`. No credential is here or in the container's environment: the
    container reaches the model, the MCP servers and the internet only through the runner's
    loopback listeners."""

    #: The protocol the step is sent with: `responses` (`POST /responses`) or `invocations`.
    protocol: Literal["responses", "invocations"] = "responses"
    #: The image the executor starts beside the runner, always `ref@sha256:<hex>`.
    image: str = Field(min_length=1, pattern=r"@sha256:[0-9a-f]{64}$")
    port: int = Field(default=8088, ge=1024, le=65535)
    readiness_timeout_s: int = Field(default=120, ge=1, le=900)
    termination_grace_s: int = Field(default=30, ge=1, le=300)
    #: `none`: the repository is not mounted; `read`: mounted read-only; `write`: the container
    #: changes it and the runner commits and pushes (it never holds a git credential).
    workspace: Literal["none", "read", "write"] = "none"
    #: Non-secret variables the version declares; reserved prefixes were refused at publication.
    env: dict[str, str] = Field(default_factory=dict)
    agent_slug: str = Field(min_length=1)
    agent_version: int = Field(ge=1)
    #: The container serves `/invocations_ws`: a session's pod relays the caller's socket to it
    #: (S41-21). A workflow step never opens it.
    socket: bool = False

    @field_validator("port")
    @classmethod
    def _not_a_loopback_listener(cls, port: int) -> int:
        if port in LOOPBACK_PORTS:
            raise ValueError(f"port {port} is one of the runner's loopback listeners (4001-4003)")
        return port


class PromptCall(Strict):
    """A step a prompt agent carries (ADR 0080): the runner runs a tool loop on the model with the
    instructions and the run's MCP tools; no coding agent, no container code. `agent.backend` is
    `prompt`."""

    max_output_tokens: int | None = Field(default=None, ge=1, le=128_000)
    tool_choice: Literal["auto", "required", "none"] = "auto"
    response_format: Literal["text", "json_object"] = "text"


class StageInput(Strict):
    """What the orchestrator hands to the runner (docs/plan/01 §1.5)."""

    schema_: Literal["choregos/StageInput/v1"] = Field(default="choregos/StageInput/v1", alias="schema")
    run_id: str
    attempt: int = Field(ge=1)
    trace_parent: str | None = None
    project: ProjectRef
    work_item: WorkItemRef
    transition: TransitionRef
    #: The repository the stage works on. **Absent** for a project without a repository: the
    #: runner then prepares an empty directory, clones nothing, pushes nothing, and the
    #: guarantees that read a diff refuse for lack of matter (ADR 0012, limit no. 1).
    repo: RepoRef | None = None
    agent: AgentRef
    model: ModelRef
    gateway_key: str | None = None
    budget: Budget
    allowed_paths: list[str] = Field(default_factory=list)
    context_pack_url: str | None = None
    playbook: PlaybookRef
    tools: ToolsRef = Field(default_factory=ToolsRef)
    #: The skills of the registry agent (ADR 0033); empty for a playbook.
    skills: list[SkillRef] = Field(default_factory=list)
    #: The knowledge bases of the registry agent (ADR 0051); empty for a playbook.
    knowledge: list[KnowledgeRef] = Field(default_factory=list)
    #: Whose memory the run reads and writes (S32-02): the project's, the agent's own within the
    #: project, or none. `project` for a playbook and before the field existed.
    memory_scope: Literal["none", "project", "agent"] = "project"
    #: The memories the run reads and writes, resolved and in precedence order (S40-11, ADR 0075).
    #: `memory_scope` stays, set to the legacy equivalent when there is one.
    memories: list[StageMemory] = Field(default_factory=list)
    permissions: Permissions = Field(default_factory=Permissions)
    context: StageContext = Field(default_factory=StageContext)
    callbacks: Callbacks
    #: Set for a job step (ADR 0056): `agent`, `model` and `playbook` are then placeholders.
    job: JobSpec | None = None
    #: Set for a step a remote agent carries over A2A (ADR 0062): `agent.backend` is `a2a`.
    remote: RemoteCall | None = None
    #: Set for a step a hosted agent carries (ADR 0080): `agent.backend` is `hosted`.
    hosted: HostedCall | None = None
    #: Set for a step a prompt agent carries (ADR 0080): `agent.backend` is `prompt`.
    prompt: PromptCall | None = None

    model_config = Strict.model_config | {"populate_by_name": True}

    @model_validator(mode="after")
    def _one_kind_of_step(self) -> StageInput:
        """A step is carried by one thing: a job, a remote agent, a hosted agent or a prompt agent
        (or, with none of them, the ACP agent of `agent`)."""
        carriers = [name for name in ("job", "remote", "hosted", "prompt") if getattr(self, name) is not None]
        if len(carriers) > 1:
            raise ValueError(f"a step is carried by one thing, not {' and '.join(carriers)}")
        return self


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


class ArtifactFile(Strict):
    """A file a step left in `.choregos/outputs/`, as the runner stored it (ADR 0049)."""

    filename: str
    type: str
    media_type: str
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    size: int = Field(ge=0)


class Artifacts(Strict):
    branch: str | None = None
    commits: list[str] = Field(default_factory=list)
    pr_url: str | None = None
    transcript_url: str | None = None
    context_pack_url: str | None = None
    reports: dict[str, str] = Field(default_factory=dict)
    #: The files of `.choregos/outputs/`, by artifact name; written by the runner, never by the agent.
    files: dict[str, ArtifactFile] = Field(default_factory=dict)


class Evidence(Strict):
    """The proofs of a stage. The named fields speak of software; `facts` does not.

    `Evidence` refuses unknown fields: a typo in `tests_run` must show. But a processed case file,
    a profile shortlist or a letter have neither tests nor coverage, and until 2026-09-24 had
    **nowhere** to record anything: the contract rejected their proofs. `facts` is that place,
    a dictionary of simple values, named by the trade, that the `evidence_facts` guarantee
    reads. Simple on purpose: a number, a boolean, a date or a word can be checked; a free
    object would become prose again.
    """

    facts: dict[str, str | int | float | bool] | None = None
    tests_passed: bool | None = None
    tests_run: int | None = None
    tests_failed: int | None = None
    coverage_delta: float | None = None
    lint: Literal["ok", "failed", "skipped"] | None = None
    typecheck: Literal["ok", "failed", "skipped"] | None = None
    security_scan: Literal["ok", "failed", "skipped"] | None = None
    diff_files: int | None = None
    diff_lines: int | None = None
    #: What the platform measured itself (ADR 0045): the names of the fields it executed or
    #: computed, and `facts.<name>` for a fact a command measured. The runner writes it, never
    #: the agent: `merge_evidence` overwrites what an agent would put there. A field absent from
    #: the list comes from the agent's story; `None`: a runner too old to say.
    measured: list[str] | None = None

    def is_complete(self) -> bool:
        """Minimal proofs expected by the `evidence_present` gate."""
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
    """Structured outputs of a stage; extra fields tolerated (custom roles)."""

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
    """What the runner returns to the orchestrator (docs/plan/01 §1.6)."""

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
    #: The label of the memory this item came from, e.g. `global:security-lessons` (S40-11); `None`
    #: for a run that reads a single memory.
    memory: str | None = None


class RelatedItem(Strict):
    key: str
    title: str
    state: str | None = None
    url: str | None = None
    summary: str | None = None


class ContextPack(Strict):
    """Memory injected into a stage. **Untrusted** content: data, never instructions."""

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
