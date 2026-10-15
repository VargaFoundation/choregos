# SPDX-License-Identifier: Apache-2.0
"""Le registre d'agents (ADR 0033) : un agent, ses versions immuables, l'épingle d'un projet."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .base import Dto
from .identite import ApiTokenCreated

SLUG = r"^[a-z][a-z0-9-]{1,62}$"

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


class AgentEvalResult(Dto):
    name: str
    passed: bool
    missing: list[str] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)
    error: str | None = None


class AgentSpec(Dto):
    """Ce qu'une version d'agent EST. Elle ne se modifie jamais : un changement publie la suivante."""

    instructions: str = Field(default="", max_length=65_536)
    model: str | None = None
    backend: str | None = None
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


class AgentCreate(Dto):
    slug: str = Field(pattern=SLUG)
    kind: Literal["internal", "external"] = "internal"
    display_name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    spec: AgentSpec = Field(default_factory=AgentSpec)
    #: `draft` until it is activated (S32-04); `active` by default, as before.
    lifecycle: Literal["draft", "active"] = "active"
    #: Who owns it (ADR 0061); `org` by default, so a client written before scopes keeps its behaviour.
    scope: CreatableScope = "org"
    #: The project's slug: required for, and only for, a `project` agent.
    project: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _a_project_agent_names_its_project(self) -> AgentCreate:
        if (self.scope == "project") != (self.project is not None):
            raise ValueError("`project` is required for, and only for, a `project` agent")
        return self


class AgentFork(Dto):
    """Fork an agent: a new agent whose version 1 is a copy of one of the source's versions (ADR 0061)."""

    slug: str = Field(pattern=SLUG)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    scope: CreatableScope
    project: str | None = Field(default=None, min_length=1)
    #: The source version to copy; absent, its latest.
    version: int | None = Field(default=None, ge=1)

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
    #: The scopes the caller may fork it into.
    fork_scopes: list[CreatableScope] = Field(default_factory=list)


class AgentCatalogueEntry(Dto):
    """Un agent que le catalogue propose (ADR 0040), et ce qu'il en est dans l'organisation."""

    slug: str
    kind: Literal["internal", "external"]
    display_name: str
    description: str | None = None
    #: Le rôle du paquet qu'il tient (`implement`, `review`…), ou `external` pour un client.
    role: str
    summary: str
    version: int
    skills: list[str] = Field(default_factory=list)
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


class AgentVersionDto(Dto):
    version: int
    spec: AgentSpec
    checksum: str
    created_by: str | None = None
    created_at: datetime | None = None


class AgentDto(Dto):
    slug: str
    kind: Literal["internal", "external"]
    display_name: str
    description: str | None = None
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


class AgentOverrides(Dto):
    """Ce qu'un projet peut changer d'une version : seulement resserrer."""

    limits: AgentLimits = Field(default_factory=AgentLimits)
    budget: AgentBudget = Field(default_factory=AgentBudget)
    #: Un sous-ensemble des outils de la version ; absent, tous.
    tools: list[str] | None = None


class ProjectAgentPut(Dto):
    version: int = Field(ge=1)
    overrides: AgentOverrides = Field(default_factory=AgentOverrides)


class ProjectAgentDto(Dto):
    agent: str
    version: int
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
    daily_budget_usd: float | None = None


class AgentMetricsSummary(Dto):
    """An agent's line in the registry over a period (S30-04): one request for the whole table."""

    agent: str
    days: int
    runs: int
    success_rate: float | None = None
    cost_usd: float = 0.0


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
