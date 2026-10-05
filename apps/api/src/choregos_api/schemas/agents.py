# SPDX-License-Identifier: Apache-2.0
"""Le registre d'agents (ADR 0033) : un agent, ses versions immuables, l'épingle d'un projet."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import Dto

SLUG = r"^[a-z][a-z0-9-]{1,62}$"


class AgentLimits(Dto):
    max_turns: int | None = Field(default=None, ge=1, le=1000)
    max_minutes: int | None = Field(default=None, ge=1, le=480)


class AgentBudget(Dto):
    #: Plafond d'un run, et de toute une journée de l'agent, en dollars.
    run_usd: float | None = Field(default=None, gt=0)
    daily_usd: float | None = Field(default=None, gt=0)


class AgentSkillRef(Dto):
    slug: str = Field(pattern=SLUG)
    #: Absente : la dernière version, lue au moment du run.
    version: int | None = Field(default=None, ge=1)


class AgentMcpServer(Dto):
    #: Le connecteur `mcp` du projet ou de l'organisation (ADR 0034).
    connector: str
    #: Les outils que l'agent peut appeler : des noms, ou des motifs `prefixe_*`.
    tools: list[str] = Field(default_factory=list)


class AgentSpec(Dto):
    """Ce qu'une version d'agent EST. Elle ne se modifie jamais : un changement publie la suivante."""

    instructions: str = Field(default="", max_length=65_536)
    model: str | None = None
    backend: str | None = None
    limits: AgentLimits = Field(default_factory=AgentLimits)
    budget: AgentBudget = Field(default_factory=AgentBudget)
    skills: list[AgentSkillRef] = Field(default_factory=list)
    mcp_servers: list[AgentMcpServer] = Field(default_factory=list)


class AgentCreate(Dto):
    slug: str = Field(pattern=SLUG)
    kind: Literal["internal", "external"] = "internal"
    display_name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    spec: AgentSpec = Field(default_factory=AgentSpec)


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


class SkillFiles(Dto):
    """Les fichiers d'une version de skill : chemin relatif → texte."""

    files: dict[str, str]


class SkillVersionDto(Dto):
    version: int
    digest: str
    created_by: str | None = None
    created_at: datetime | None = None
    #: Rendus par la lecture d'UNE version ; la liste n'en porte pas.
    files: dict[str, str] | None = None


class SkillDto(Dto):
    slug: str
    description: str | None = None
    status: str
    latest_version: int
    #: Les agents dont une version porte la skill : `agent@version`.
    used_by: list[str] = Field(default_factory=list)
    versions: list[SkillVersionDto] | None = None


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
