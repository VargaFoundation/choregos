# SPDX-License-Identifier: Apache-2.0
"""Le registre d'agents (ADR 0033) : un agent, ses versions immuables, l'épingle d'un projet."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from choregos_core.memory_bindings import Binding, binding_problems, effective_bindings
from choregos_core.network import validate_pattern
from pydantic import (
    Field,
    SerializerFunctionWrapHandler,
    computed_field,
    field_validator,
    model_serializer,
    model_validator,
)

from .base import Dto
from .identite import ApiTokenCreated

SLUG = r"^[a-z][a-z0-9-]{1,62}$"

#: A tag (S40-10): a lowercase slug of at most 32 characters; an agent carries at most `MAX_TAGS`.
TAG = r"^[a-z0-9][a-z0-9-]{0,31}$"
MAX_TAGS = 10
AgentTag = Annotated[str, Field(pattern=TAG)]

#: The longest message a version carries (S40-12).
MESSAGE_MAX = 500


def _no_duplicate_tags(tags: list[str] | None) -> list[str] | None:
    if tags is not None:
        seen: set[str] = set()
        for tag in tags:
            if tag in seen:
                raise ValueError(f"duplicate tag: {tag}")
            seen.add(tag)
    return tags


#: Who owns an agent (ADR 0061). `platform` is never asked for: a common agent comes from the catalogue.
AgentScope = Literal["platform", "org", "project", "personal"]
CreatableScope = Literal["org", "project", "personal"]


class AgentLimits(Dto):
    max_turns: int | None = Field(default=None, ge=1, le=1000)
    max_minutes: int | None = Field(default=None, ge=1, le=480)


class AgentBudget(Dto):
    #: Plafond d'un run, et de toute une journée de l'agent, en dollars.
    run_usd: float | None = Field(default=None, gt=0)
    daily_usd: float | None = Field(default=None, gt=0)
    #: A calendar month (UTC). At 100 % the agent is suspended (S27-09).
    monthly_usd: float | None = Field(default=None, gt=0)


class AgentSkillRef(Dto):
    slug: str = Field(pattern=SLUG)
    #: Absente : la dernière version, lue au moment du run.
    version: int | None = Field(default=None, ge=1)


class AgentMcpServer(Dto):
    #: Le connecteur `mcp` du projet ou de l'organisation (ADR 0034).
    connector: str
    #: Les outils que l'agent peut appeler : des noms, ou des motifs `prefixe_*`.
    tools: list[str] = Field(default_factory=list)


class AgentKnowledgeRef(Dto):
    """A knowledge base an agent reads (ADR 0051): by its slug; its documents are read at each run."""

    slug: str = Field(pattern=SLUG)


class AgentMemoryBinding(Dto):
    """A memory an agent version binds (S40-11, ADR 0075): global to the organisation, or a memory of
    whichever project the run is in; read, or read and write (at most one write in a list)."""

    scope: Literal["global", "project"]
    slug: str = Field(pattern=SLUG)
    mode: Literal["read", "write"] = "read"

    def to_binding(self) -> Binding:
        return Binding(self.scope, self.slug, self.mode)


class AgentSampleInput(Dto):
    """A sample work item an agent is tried or evaluated on (S32-03)."""

    title: str = Field(default="", max_length=500)
    body: str = Field(default="", max_length=20_000)


class AgentEvalCase(Dto):
    """A deterministic case of an agent's eval suite (S32-03, ADR 0020 level 1)."""

    name: str = Field(min_length=1, max_length=120)
    input: AgentSampleInput = Field(default_factory=AgentSampleInput)
    must_contain: list[str] = Field(default_factory=list)
    must_not_contain: list[str] = Field(default_factory=list)
    #: A live case (S40-02d) runs as a capped trial on the real model and asserts on what the run
    #: produced; the level-1 gate leaves it out (it has no output to read before a run).
    live: bool = False
    #: The status the run's result must have (a case may expect the agent to stop and ask).
    expect_status: Literal["done", "blocked"] = "done"
    max_cost_usd: float | None = Field(default=None, gt=0, le=0.5)


class AgentEvalResult(Dto):
    name: str
    passed: bool
    missing: list[str] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)
    error: str | None = None


class AcpRuntime(Dto):
    """A coding agent that speaks ACP (ADR 0002): one of the backends the runner ships."""

    kind: Literal["acp"] = "acp"
    backend: str | None = None


class CodeRuntime(Dto):
    """An agent written in code (ADR 0063): an ACP agent in an image the operator named.

    `image` is a NAME in the operator's `CHOREGOS_AGENT_IMAGES`, never a reference: whoever writes
    an agent does not choose what the platform pulls. `command` is the argv the runner starts in
    that image; it speaks ACP on stdio (the `choregos-agent-sdk` does it for a Python agent).
    """

    kind: Literal["code"] = "code"
    image: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    command: list[str] = Field(min_length=1, max_length=64)
    api_format: Literal["openai", "anthropic"] = "openai"

    @field_validator("command")
    @classmethod
    def _no_empty_argument(cls, command: list[str]) -> list[str]:
        if any(not part or len(part) > 1024 for part in command):
            raise ValueError("every argument of the command is 1 to 1024 characters")
        return command


class AgentImageInfo(Dto):
    """An image an operator named for code agents; the reference itself is never returned."""

    name: str
    digest_pinned: bool


class A2aDeclaredCost(Dto):
    """What a remote agent costs, as the organisation DECLARES it (ADR 0062): A2A reports no figure."""

    per_task_usd: float | None = Field(default=None, ge=0)
    per_minute_usd: float | None = Field(default=None, ge=0)


class A2aPinnedCard(Dto):
    """The card as it was when the version was published: the API fills it, never the caller."""

    url: str
    protocol_version: str
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    skills: list[str] = Field(default_factory=list)


class A2aRuntime(Dto):
    """An agent that runs elsewhere and is reached over A2A (ADR 0062), e.g. Atlassian's Rovo.

    The step runs in the runner pod (it calls the endpoint), not in the orchestrator. `credential`
    names an organisation remote credential; `pinned_card` is what publication read. `model` and
    `backend` of the spec mean nothing here: the remote agent brings its own model.
    """

    kind: Literal["a2a"] = "a2a"
    card_url: str = Field(pattern=r"^https://", max_length=2048)
    #: One skill of the card the step asks for; absent leaves the choice to the remote agent.
    skill: str | None = Field(default=None, max_length=200)
    #: The organisation's remote credential; absent only for a card that asks for none.
    credential: str | None = Field(default=None, pattern=SLUG)
    timeout_minutes: int = Field(default=30, ge=1, le=480)
    declared_cost: A2aDeclaredCost = Field(default_factory=A2aDeclaredCost)
    #: `text`: instructions and task as one text part. `text+files`: the declared inputs go along.
    input: Literal["text", "text+files"] = "text"
    pinned_card: A2aPinnedCard | None = None


AgentRuntime = Annotated[AcpRuntime | CodeRuntime | A2aRuntime, Field(discriminator="kind")]


class AgentNetwork(Dto):
    """What one agent version may reach (ADR 0070). It only ever narrows the project's network and
    the organisation's: a host they do not allow refuses the publication and the run.

    Every field defaults to `None`, and a version that leaves them all there carries no `network`
    in its digest: versions published before the field existed keep theirs."""

    #: `None`: the project's list; `[]`: no internet; a list: only these hosts.
    domains: list[str] | None = None
    #: New connections a run may open to one host per minute.
    per_host_per_minute: int | None = Field(default=None, ge=1)
    #: `False` switches the agent CLI's own web tools off; `None` or `True`: as the policy says.
    builtin_web_tools: bool | None = None

    @field_validator("domains")
    @classmethod
    def _patterns(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        errors: list[str] = []
        patterns: list[str] = []
        for entry in value:
            try:
                patterns.append(validate_pattern(entry))
            except ValueError as error:
                errors.append(str(error))
        if errors:
            raise ValueError("; ".join(errors))
        return list(dict.fromkeys(patterns))


class AgentSpec(Dto):
    """Ce qu'une version d'agent EST. Elle ne se modifie jamais : un changement publie la suivante."""

    instructions: str = Field(default="", max_length=65_536)
    model: str | None = None
    backend: str | None = None
    #: How the version runs (ADR 0063); absent means `acp` with `backend`.
    runtime: AgentRuntime | None = None
    limits: AgentLimits = Field(default_factory=AgentLimits)
    budget: AgentBudget = Field(default_factory=AgentBudget)
    skills: list[AgentSkillRef] = Field(default_factory=list)
    mcp_servers: list[AgentMcpServer] = Field(default_factory=list)
    #: The knowledge bases the agent reads (ADR 0051), each served to the run's project or the run fails.
    knowledge: list[AgentKnowledgeRef] = Field(default_factory=list)
    #: The agent's eval suite (S32-03): it gates the publication of the next version.
    evals: list[AgentEvalCase] = Field(default_factory=list)
    #: Whose memory a run reads and writes (S32-02): `project` (shared, the default), `agent` (its
    #: own, within the project) or `none`.
    memory: Literal["none", "project", "agent"] = "project"
    #: The hosts the agent may reach and how fast (ADR 0070).
    network: AgentNetwork = Field(default_factory=AgentNetwork)

    #: The memories a run reads and writes (S40-11, ADR 0075), in precedence order; at most 8, one
    #: `write`. Absent, the legacy `memory` field decides (`project` -> the project's shared memory).
    memories: list[AgentMemoryBinding] | None = Field(default=None, max_length=8)

    @model_validator(mode="after")
    def _memories_are_coherent(self) -> AgentSpec:
        if self.memories is None:
            return self
        if self.memory != "project":
            raise ValueError("`memory` is superseded by `memories`: send one of them, not both")
        if problems := binding_problems([binding.to_binding() for binding in self.memories]):
            raise ValueError("; ".join(problems))
        return self

    @model_serializer(mode="wrap")
    def _leave_out_absent_memories(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        """`memories` is left out while it is unset: a version published before the field existed keeps
        its stored spec, its digest and its response byte for byte."""
        data: dict[str, object] = handler(self)
        if data.get("memories") is None:
            data.pop("memories", None)
        return data

    def effective_memories(self) -> list[AgentMemoryBinding]:
        """What a run binds: `memories`, or what the legacy `memory` means."""
        return [
            AgentMemoryBinding(scope=b.scope, slug=b.slug, mode=b.mode)
            for b in effective_bindings(
                None if self.memories is None else [m.to_binding() for m in self.memories], self.memory
            )
        ]

    @model_validator(mode="after")
    def _one_way_to_say_the_backend(self) -> AgentSpec:
        """A code agent runs as the `code` backend, and an ACP runtime that names a backend must
        agree with the top-level `backend`: two answers to one question are refused."""
        if isinstance(self.runtime, CodeRuntime) and self.backend:
            raise ValueError("a `code` runtime fixes the backend: `backend` must be empty")
        if isinstance(self.runtime, A2aRuntime):
            if self.backend or self.model:
                raise ValueError("an `a2a` runtime brings its own model: `backend` and `model` must be empty")
            if self.skills or self.knowledge or self.mcp_servers:
                raise ValueError(
                    "an `a2a` runtime runs elsewhere: it uses none of the platform's skills, knowledge "
                    "bases or MCP servers"
                )
        if (
            isinstance(self.runtime, AcpRuntime)
            and self.runtime.backend
            and self.backend
            and self.runtime.backend != self.backend
        ):
            raise ValueError("`backend` and `runtime.backend` disagree: set only one")
        return self


# ───────────────────────────── release channels (S40-02, ADR 0071) ─────────────────────────────

Channel = Literal["stable", "candidate"]
ReleaseKind = Literal["publish", "promote", "rollback", "withdraw", "baseline"]
ReleaseStatus = Literal["done", "pending_approval", "rejected", "cancelled", "superseded"]


class AgentStableGates(Dto):
    """What a revision must satisfy before it reaches production (a rollback skips them)."""

    #: The revision must be the current candidate.
    require_candidate: bool = False
    #: Finished runs of the candidate since it became candidate.
    min_candidate_runs: int = Field(default=0, ge=0, le=10_000)
    min_success_rate: float | None = Field(default=None, ge=0, le=1)
    min_soak_hours: int | None = Field(default=None, ge=0, le=720)
    #: How recent the human's authentication must be, in minutes.
    step_up_minutes: int = Field(default=10, ge=1, le=120)
    #: The latest finished live evaluation of the revision must score at least this (S40-02d).
    min_live_score: float | None = Field(default=None, ge=0, le=1)
    #: People other than the requester who must approve a promotion before production moves (S40-02b).
    approvals: int = Field(default=0, ge=0, le=3)
    #: Neither the requester nor the revision's author may approve.
    separation_of_duties: bool = True


class AgentCandidateGates(Dto):
    """What a revision must satisfy before it is sent to testing by hand (not when saving does it)."""

    min_live_score: float | None = Field(default=None, ge=0, le=1)


class AgentReleasePolicy(Dto):
    """What saving a revision does, and the gates of production."""

    #: `stable`: it goes to production (today's behaviour); `candidate`: to testing; `none`: nowhere.
    on_publish: Literal["stable", "candidate", "none"] = "stable"
    stable: AgentStableGates = Field(default_factory=AgentStableGates)
    candidate: AgentCandidateGates = Field(default_factory=AgentCandidateGates)
    #: The most one live evaluation may spend in all, in dollars.
    max_eval_usd: float = Field(default=2.0, gt=0, le=10)

    @model_validator(mode="after")
    def _a_revision_published_to_production_is_never_a_candidate(self) -> AgentReleasePolicy:
        gates = self.stable
        if self.on_publish == "stable" and (
            gates.require_candidate
            or gates.min_candidate_runs > 0
            or gates.min_success_rate is not None
            or gates.min_soak_hours is not None
            or gates.min_live_score is not None
        ):
            raise ValueError(
                "`on_publish: stable` sends a revision straight to production, so it is never a candidate: "
                "it cannot be combined with `require_candidate` or a candidate threshold"
            )
        return self


class AgentEvaluationCreate(Dto):
    """Run a revision's live cases as capped trials, in one project."""

    project: str = Field(min_length=1)


class AgentEvaluationCase(Dto):
    name: str
    run_id: str
    status: Literal["running", "passed", "failed", "not_run"]
    passed: bool | None = None
    budget_usd: float = 0.0
    cost_usd: float = 0.0
    reason: str | None = None


class AgentEvaluation(Dto):
    id: str
    version: int
    project: str
    status: Literal["running", "passed", "failed", "not_run"]
    #: Passed cases over live cases; `None` while running and when nothing ran.
    score: float | None = None
    budget_usd: float
    started_by: str | None = None
    started_at: datetime
    cases: list[AgentEvaluationCase] = Field(default_factory=list)


class AgentReleaseCreate(Dto):
    """Point a channel at a revision."""

    channel: Channel
    #: `null` with `candidate` withdraws the candidate; `null` with `stable` promotes the current candidate.
    version: int | None = Field(default=None, ge=1)
    #: Candidate only: the share of work items served the candidate; absent keeps the current share.
    percent: int | None = Field(default=None, ge=0, le=100)
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _the_share_belongs_to_testing(self) -> AgentReleaseCreate:
        if self.percent is not None and self.channel != "candidate":
            raise ValueError("`percent` only applies to the `candidate` channel")
        return self


class AgentReleaseCheck(Dto):
    name: str
    required: bool = True
    #: `None`: not applicable.
    passed: bool | None = None
    detail: str | None = None


class AgentReleaseVote(Dto):
    """One person's answer to a release that waits for approval."""

    by: str
    decision: Literal["approve", "reject", "cancel"]
    reason: str | None = None
    at: datetime
    #: How old the person's sign-in was, in seconds, when they decided.
    auth_age_seconds: int | None = None


class AgentReleaseDecision(Dto):
    decision: Literal["approve", "reject", "cancel"]
    #: Required for a rejection.
    reason: str | None = Field(default=None, max_length=500)


class AgentReleaseDto(Dto):
    id: str
    agent: str
    channel: Channel
    kind: ReleaseKind
    version: int | None = None
    previous_version: int | None = None
    percent: int | None = None
    status: ReleaseStatus = "done"
    #: How many other people must approve before production moves; 0 for a release that did not wait.
    approvals_required: int = 0
    decisions: list[AgentReleaseVote] = Field(default_factory=list)
    requested_by: str | None = None
    requested_at: datetime
    done_at: datetime | None = None
    reason: str | None = None
    checks: list[AgentReleaseCheck] = Field(default_factory=list)


class AgentChannelStats(Dto):
    version: int
    runs: int = 0
    succeeded: int = 0
    failed: int = 0
    success_rate: float | None = None
    since: datetime | None = None


class AgentReleaseReadiness(Dto):
    """What releasing a revision to a channel would check, without doing it."""

    version: int
    channel: Channel
    kind: ReleaseKind
    ready: bool
    checks: list[AgentReleaseCheck] = Field(default_factory=list)
    candidate: AgentChannelStats | None = None
    production: AgentChannelStats | None = None


class AgentExposedProject(Dto):
    project: str
    source: Literal["pin", "pin_channel", "project_channel"]
    channel: Channel | None = None
    version: int


class AgentExposure(Dto):
    """Who is served what: production, testing, and the projects that chose their own."""

    stable_version: int | None = None
    candidate_version: int | None = None
    candidate_percent: int = 0
    projects: list[AgentExposedProject] = Field(default_factory=list)


class ProjectAgentChannel(Dto):
    channel: Channel


class AgentTrialCreate(Dto):
    """Try a draft version on a sample input, in one project, under a capped budget (S32-03)."""

    project: str = Field(min_length=1)
    spec: AgentSpec
    input: AgentSampleInput
    budget_usd: float | None = Field(default=None, gt=0)
    #: The agent the draft would become a version of: its published suite runs too.
    agent: str | None = None


class AgentTrial(Dto):
    run_id: str
    work_item_id: str
    project: str
    budget_usd: float
    status: str
    evals: list[AgentEvalResult] = Field(default_factory=list)


#: Slugs no agent may take: `/agents/new` is a static page of the console and would shadow it (S39-05).
RESERVED_SLUGS = frozenset({"new"})


def check_remote_kind(kind: str, spec: AgentSpec) -> None:
    """`remote` and an `a2a` runtime come together: one without the other is a mistake, not a default."""
    is_a2a = isinstance(spec.runtime, A2aRuntime)
    if kind == "remote" and not is_a2a:
        raise ValueError("a `remote` agent needs a `runtime` of kind `a2a`")
    if kind != "remote" and is_a2a:
        raise ValueError("an `a2a` runtime belongs to an agent of kind `remote`")


class AgentCreate(Dto):
    slug: str = Field(pattern=SLUG)
    #: `internal`: run by the platform. `external`: a client of the MCP door. `remote`: an agent
    #: the platform calls over A2A (ADR 0062); it needs an `a2a` runtime, and only that kind has one.
    kind: Literal["internal", "external", "remote"] = "internal"
    display_name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    spec: AgentSpec = Field(default_factory=AgentSpec)
    #: `draft` until it is activated (S32-04); `active` by default, as before.
    lifecycle: Literal["draft", "active"] = "active"
    #: Who owns it (ADR 0061); `org` by default, so a client written before scopes keeps its behaviour.
    scope: CreatableScope = "org"
    #: The project's slug: required for, and only for, a `project` agent.
    project: str | None = Field(default=None, min_length=1)
    #: Labels people find the agent by (S40-10); never part of a version.
    tags: list[AgentTag] = Field(default_factory=list, max_length=MAX_TAGS)
    #: Why version 1 exists (S40-12); absent, `Created`.
    message: str | None = Field(default=None, max_length=MESSAGE_MAX)

    _tags_are_unique = field_validator("tags")(_no_duplicate_tags)
    #: What saving a revision does (S40-02); absent, the defaults: it goes to production.
    release_policy: AgentReleasePolicy | None = None

    @model_validator(mode="after")
    def _a_project_agent_names_its_project(self) -> AgentCreate:
        if (self.scope == "project") != (self.project is not None):
            raise ValueError("`project` is required for, and only for, a `project` agent")
        return self

    @field_validator("slug")
    @classmethod
    def _slug_is_not_reserved(cls, value: str) -> str:
        if value in RESERVED_SLUGS:
            raise ValueError(f"`{value}` is reserved: the console's `/agents/{value}` page is not an agent")
        return value

    @model_validator(mode="after")
    def _remote_means_a2a(self) -> AgentCreate:
        check_remote_kind(self.kind, self.spec)
        return self


class AgentFork(Dto):
    """Fork an agent: a new agent whose version 1 is a copy of one of the source's versions (ADR 0061)."""

    slug: str = Field(pattern=SLUG)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    scope: CreatableScope
    project: str | None = Field(default=None, min_length=1)
    #: The source version to copy; absent, its latest.
    version: int | None = Field(default=None, ge=1)
    #: Why version 1 exists (S40-12); absent, `Forked from <slug>@<version>`.
    message: str | None = Field(default=None, max_length=MESSAGE_MAX)

    @model_validator(mode="after")
    def _a_project_fork_names_its_project(self) -> AgentFork:
        if (self.scope == "project") != (self.project is not None):
            raise ValueError("`project` is required for, and only for, a `project` fork")
        return self


class AgentScopeChange(Dto):
    """Promote an agent: personal to a project or the organisation, a project's to the organisation."""

    scope: CreatableScope
    project: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _a_move_to_a_project_names_it(self) -> AgentScopeChange:
        if (self.scope == "project") != (self.project is not None):
            raise ValueError("`project` is required for, and only for, a move to a `project`")
        return self


class AgentUpstream(Dto):
    """What a fork was copied from, and whether the source moved on since (ADR 0061)."""

    slug: str
    #: The version the fork copied.
    version: int
    #: Absent when the source is no longer visible to the caller (or deleted).
    scope: AgentScope | None = None
    latest_version: int | None = None
    #: The source published a version newer than the one copied.
    behind: bool = False
    available: bool = True


class AgentPermissions(Dto):
    """What the caller may do with an agent, decided by the server (ADR 0061)."""

    #: Publish a version, edit its metadata, move it through its life.
    publish: bool = False
    #: Suspend, revoke, change its owner, attach clients.
    manage: bool = False
    #: Pin it on a project the caller owns.
    pin: bool = False
    #: Move its channels: send a revision to testing, to production, roll back (S40-02).
    release: bool = False
    #: The scopes the caller may fork it into.
    fork_scopes: list[CreatableScope] = Field(default_factory=list)


class AgentDraftRequest(Dto):
    """What a person wants an agent for (S39-05): the description is DATA for the drafting model."""

    display_name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=10, max_length=4000)


class AgentDraft(Dto):
    """A proposal, never saved: the console pre-fills the builder with it and a person creates the agent."""

    display_name: str
    slug: str = Field(pattern=SLUG)
    description: str
    spec: AgentSpec
    #: An input to try the agent on (the builder's "Try it").
    sample: AgentSampleInput
    #: What the platform changed or dropped in the model's answer, in words.
    notes: list[str] = Field(default_factory=list)
    #: The model that drafted it.
    model: str


class AgentCatalogueEntry(Dto):
    """Un agent que le catalogue propose (ADR 0040), et ce qu'il en est dans l'organisation."""

    slug: str
    kind: Literal["internal", "external", "remote"]
    display_name: str
    description: str | None = None
    #: Le rôle du paquet qu'il tient (`implement`, `review`…), ou `external` pour un client.
    role: str
    summary: str
    version: int
    skills: list[str] = Field(default_factory=list)
    #: Labels the entry carries (S40-10): installed with the agent, added on upgrade, never removed.
    tags: list[str] = Field(default_factory=list)
    #: Pour un client externe : son identifiant sur la page des clients, et d'où il appelle.
    client: str | None = None
    reach: Literal["always", "cloud"] | None = None
    installed: bool
    installed_version: int | None = None
    #: Un agent du même nom existe, mais l'organisation l'a créé elle-même : le catalogue n'y touche pas.
    own_agent: bool = False
    #: The scope of the agent of this name in the organisation, if any (ADR 0061).
    scope: AgentScope | None = None
    update_available: bool = False
    #: Faux pour un client qui appelle depuis le cloud de son éditeur quand la porte ne l'accepte pas
    #: encore (OAuth, client enregistré, https) : la page le dit, et ne le propose pas.
    offered: bool = True
    unavailable_reason: str | None = None


class AgentCatalogueConnect(Dto):
    #: Un jeton `mcp:read` au lieu de `mcp:write` : le client lit, il n'ouvre ni ne propose rien.
    read_only: bool = False
    expires_in_days: int | None = Field(default=30, ge=1, le=365)


class AgentCatalogueConnection(Dto):
    """Un client connecté : son agent externe, et de quoi le configurer — un jeton rendu UNE fois,
    ou le client OAuth que l'IdP a enregistré (claude.ai, ChatGPT)."""

    agent: AgentDto
    token: ApiTokenCreated | None = None
    oauth_client_id: str | None = None


class AgentCatalogueInstall(Dto):
    #: Déjà installé : publier la version suivante depuis le catalogue (rien si le texte n'a pas changé).
    upgrade: bool = False


class AgentPatch(Dto):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    #: `revoked` est définitif ; `suspended` se lève.
    status: Literal["active", "suspended", "revoked"] | None = None
    expires_at: datetime | None = None
    #: Replaces the agent's tags; absent or null leaves them as they are (S40-10).
    tags: list[AgentTag] | None = Field(default=None, max_length=MAX_TAGS)

    _tags_are_unique = field_validator("tags")(_no_duplicate_tags)


class AgentVersionDto(Dto):
    version: int
    spec: AgentSpec
    checksum: str
    created_by: str | None = None
    created_at: datetime | None = None
    #: Why the version exists, in its author's words; absent for a version published before messages.
    message: str | None = None
    #: The version this one copies, when it is a restore.
    restored_from: int | None = None
    #: The projects the caller may see that pin this version.
    pinned_by: list[str] = Field(default_factory=list)
    #: The channels this revision sits on now (S40-02).
    channels: list[Channel] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_memories(self) -> list[AgentMemoryBinding]:
        """The memories a run of this version binds, derived from `memory` when `memories` is absent."""
        return self.spec.effective_memories()


class AgentVersionPublish(Dto):
    """Publish the next version from the one the author read: refused when the latest has moved (S40-12)."""

    spec: AgentSpec
    message: str = Field(min_length=1, max_length=MESSAGE_MAX)
    #: The latest version the author started from (0 for an agent with none).
    base_version: int = Field(ge=0)

    @field_validator("message")
    @classmethod
    def _message_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("a message says what changed and why")
        return value


class AgentVersionRestore(Dto):
    """Copy an older version as the next one; nothing is rewritten or deleted (S40-12)."""

    message: str | None = Field(default=None, max_length=MESSAGE_MAX)
    base_version: int = Field(ge=1)

    @field_validator("message")
    @classmethod
    def _message_is_stripped(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class AgentDto(Dto):
    slug: str
    kind: Literal["internal", "external", "remote"]
    display_name: str
    description: str | None = None
    #: Labels people find the agent by (S40-10); apart from its versions.
    tags: list[str] = Field(default_factory=list)
    status: str
    owner: str | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    latest_version: int
    created_at: datetime | None = None
    versions: list[AgentVersionDto] | None = None
    #: draft, active, deprecated or retired (S32-04) — apart from the operational `status`.
    lifecycle: Literal["draft", "active", "deprecated", "retired"] = "active"
    retired_at: datetime | None = None
    #: Its last run, all projects together; `None` if it never ran.
    last_run_at: datetime | None = None
    #: No run for 60 days (since its creation if it never ran): a candidate for retirement.
    unused: bool = False
    #: Who owns it (ADR 0061).
    scope: AgentScope = "org"
    #: The slug of its project, for a `project` agent.
    project: str | None = None
    #: Set on a fork.
    forked_from: AgentUpstream | None = None
    #: What the caller may do, and the server's own sentence for what they may not.
    can: AgentPermissions = Field(default_factory=AgentPermissions)
    cannot_reason: str | None = None
    #: Production and testing (S40-02, ADR 0071).
    stable_version: int | None = None
    candidate_version: int | None = None
    candidate_percent: int = 0
    candidate_since: datetime | None = None
    release_policy: AgentReleasePolicy = Field(default_factory=AgentReleasePolicy)
    #: The release waiting for approval, if any (S40-02b).
    pending_release: AgentReleaseDto | None = None


class AgentOverrides(Dto):
    """Ce qu'un projet peut changer d'une version : seulement resserrer."""

    limits: AgentLimits = Field(default_factory=AgentLimits)
    budget: AgentBudget = Field(default_factory=AgentBudget)
    #: Un sous-ensemble des outils de la version ; absent, tous.
    tools: list[str] | None = None


class ProjectAgentPut(Dto):
    #: Exactly one of `version` (a fixed revision) and `channel` (follow production or testing).
    version: int | None = Field(default=None, ge=1)
    channel: Channel | None = None
    overrides: AgentOverrides = Field(default_factory=AgentOverrides)

    @model_validator(mode="after")
    def _a_pin_is_a_revision_or_a_channel(self) -> ProjectAgentPut:
        if (self.version is None) == (self.channel is None):
            raise ValueError("give exactly one of `version` (a fixed revision) and `channel`")
        return self


class ProjectAgentDto(Dto):
    agent: str
    #: The revision served now (the channel's, for a channel pin).
    version: int
    #: Set when the pin follows a channel; `None` for a fixed revision.
    channel: Channel | None = None
    overrides: AgentOverrides
    #: La version épinglée, surcharges appliquées : ce qui tournera.
    effective: AgentSpec
    #: Said when the agent is deprecated: the pin holds, but the agent is on its way out.
    warning: str | None = None


class SkillFiles(Dto):
    """Les fichiers d'une version de skill : chemin relatif → texte."""

    files: dict[str, str]


class SkillSourceDto(Dto):
    """Where a version imported from git comes from (S32-05)."""

    repo: str
    #: The full commit SHA the version is pinned to.
    sha: str
    #: The skill's folder in the repository; `""` is the root.
    path: str
    #: An SPDX identifier, `LicenseRef-proprietary`, `LicenseRef-unknown`, or `None` if none found.
    licence: str | None = None
    #: `<id>@<catalogue_version>` when the version was installed from the skills catalogue (ADR 0065).
    catalogue: str | None = None
    #: For a catalogue install: `bundled` (the image) or `git` (the pinned commit).
    delivery: Literal["bundled", "git"] | None = None


class SkillFinding(Dto):
    """What the gate found in a version (S32-06, ADR 0050)."""

    code: str
    severity: Literal["info", "warning", "critical"]
    detail: str
    file: str | None = None
    line: int | None = None


class SkillVersionDto(Dto):
    version: int
    digest: str
    created_by: str | None = None
    created_at: datetime | None = None
    #: Rendus par la lecture d'UNE version ; la liste n'en porte pas.
    files: dict[str, str] | None = None
    source: SkillSourceDto | None = None
    #: `low`, `medium`, `high`; `None` before the gate existed.
    risk: Literal["low", "medium", "high"] | None = None
    #: `approved`, or `pending_review` until an administrator who is not the importer approves it.
    review_status: Literal["approved", "pending_review"] = "approved"
    findings: list[SkillFinding] = Field(default_factory=list)
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None


class SkillSources(Dto):
    """The repositories a project owner may import skills from without an administrator."""

    sources: list[str] = Field(max_length=100)
    #: True when the organisation has not written its own list: these are the defaults.
    default: bool = False


class SkillGitRequest(Dto):
    repo_url: str = Field(max_length=300)
    #: The folder of the repository holding one skill, or several skill folders; `""` is the root.
    path: str = Field(default="", max_length=300)


class SkillGitPreviewRequest(SkillGitRequest):
    #: A branch, a tag or a commit: the preview resolves it to the SHA an import pins.
    ref: str = Field(min_length=1, max_length=200)


class SkillGitImport(SkillGitRequest):
    #: A full commit SHA (40 hex characters). A branch or a tag is refused: it can move.
    sha: str = Field(min_length=1, max_length=200)
    #: The names of the skills to import, among those the preview listed.
    skills: list[str] = Field(min_length=1, max_length=100)


class SkillCandidate(Dto):
    """A folder with a `SKILL.md` found at that commit."""

    path: str
    name: str | None = None
    description: str | None = None
    files: int
    bytes: int
    licence: str | None = None
    #: Why it cannot be imported, if it cannot.
    problem: str | None = None
    #: The version of the library's skill of the same name, if there is one.
    existing_version: int | None = None
    risk: Literal["low", "medium", "high"] | None = None
    findings: list[SkillFinding] = Field(default_factory=list)


class SkillGitPreview(Dto):
    repo: str
    ref: str
    sha: str
    path: str
    #: Whether the repository is in the organisation's allowlist.
    allowed: bool
    skills: list[SkillCandidate]


class SkillUpdate(Dto):
    """What a newer commit of the skill's repository would change. Never applied by itself: an
    import of that SHA publishes it, after a person read this."""

    slug: str
    version: int
    source: SkillSourceDto
    ref: str
    sha: str
    up_to_date: bool
    #: The digest the new version would have; equal to the current one when nothing changed.
    digest: str | None = None
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    modified: list[str] = Field(default_factory=list)
    #: Set when the skill is no longer at its path, or no longer passes the checks.
    problem: str | None = None


class SkillDto(Dto):
    slug: str
    description: str | None = None
    status: str
    latest_version: int
    #: Les agents dont une version porte la skill : `agent@version`.
    used_by: list[str] = Field(default_factory=list)
    versions: list[SkillVersionDto] | None = None
    #: The gate of the latest version.
    risk: Literal["low", "medium", "high"] | None = None
    review_status: Literal["approved", "pending_review"] = "approved"


class AgentProjectMetrics(Dto):
    project: str
    runs: int
    cost_usd: float


class AgentVersionMetrics(Dto):
    version: int
    runs: int = 0
    succeeded: int = 0
    failed: int = 0
    success_rate: float | None = None
    cost_usd: float = 0.0


class AgentMetrics(Dto):
    """Ce qu'un agent a fait sur la période : ses runs, leur issue, ce qu'ils ont coûté."""

    agent: str
    days: int
    runs: int
    succeeded: int
    failed: int
    #: Réussis / terminés ; vide tant qu'aucun run n'est terminé.
    success_rate: float | None = None
    cost_usd: float = 0.0
    #: `model` et `tool` : un appel d'outil compte dans le coût d'un agent.
    cost_by_kind: dict[str, float] = Field(default_factory=dict)
    by_project: list[AgentProjectMetrics] = Field(default_factory=list)
    spent_today_usd: float = 0.0
    #: Production's daily budget (S40-02).
    daily_budget_usd: float | None = None
    by_version: list[AgentVersionMetrics] = Field(default_factory=list)


class AgentMetricsSummary(Dto):
    """An agent's line in the registry over a period (S30-04): one request for the whole table."""

    agent: str
    days: int
    runs: int
    success_rate: float | None = None
    cost_usd: float = 0.0


class AgentResolution(Dto):
    """Which agent version a run would load for a reference (S39-13), through the runtime's resolver."""

    ref: str
    slug: str
    status: Literal["resolved", "not_installed", "unavailable"]
    version: int | None = None
    #: True when the version comes from the project's pin (no explicit `@N` in the reference).
    pinned: bool = False
    #: Why this revision (S40-02): the reference, a pin, a channel pin, the project's switch, a canary
    #: share, production, or the latest (an agent that has no production pointer).
    source: (
        Literal["explicit", "pin", "pin_channel", "project_channel", "canary", "stable", "latest"] | None
    ) = None
    channel: Channel | None = None
    candidate_version: int | None = None
    candidate_percent: int | None = None
    display_name: str | None = None
    lifecycle: str | None = None
    #: For `not_installed`: publishing the workflow installs the agent from the catalogue (ADR 0040).
    in_catalogue: bool = False
    reason: str | None = None


class ImplicitAgent(Dto):
    """A workflow actor that runs as an agent without naming one of the registry (S30-04)."""

    workflow: str
    actor: str
    role: str | None = None
    model: str | None = None
    backend: str | None = None
    max_turns: int | None = None
    max_minutes: int | None = None


class AgentCredentialCreate(Dto):
    """Un jeton `mcp:*` de la porte (son id), ou un client OAuth (son `client_id`, l'`azp` du jeton)."""

    kind: Literal["token", "oauth_client"]
    token_id: str | None = None
    client_id: str | None = Field(default=None, min_length=1, max_length=200)


class AgentCredentialDto(Dto):
    id: str
    kind: str
    token_id: str | None = None
    client_id: str | None = None
    created_by: str | None = None
    created_at: datetime | None = None
    revoked_at: datetime | None = None
    #: Ce que le jeton rattaché dit de son dernier appel : la console montre un client « connecté ».
    token_name: str | None = None
    last_used_at: datetime | None = None
    last_client: str | None = None
    #: The slug of the agent the client is attached to (the organisation's list carries it).
    agent: str | None = None


class AgentLifecycleChange(Dto):
    """A move in the agent's life (S32-04). `retired` is final and runs its checklist."""

    to: Literal["active", "deprecated", "retired"]
    #: Retire although runs of the agent are in flight (an administrator's call, audited).
    force: bool = False
    reason: str | None = Field(default=None, max_length=500)


class AgentLifecycleResult(Dto):
    agent: AgentDto
    #: Retirement's checklist, as executed.
    revoked_credentials: int = 0
    unpinned_projects: list[str] = Field(default_factory=list)
    #: The work items whose run was in flight when the retirement was forced.
    running_work_items: list[str] = Field(default_factory=list)
    forced: bool = False


class AgentOwnerChange(Dto):
    """The new owner, a member of the organisation; the agent keeps its credentials."""

    email: str = Field(min_length=3, max_length=320)
