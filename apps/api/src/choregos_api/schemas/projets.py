# SPDX-License-Identifier: Apache-2.0
"""Projets, provisioning, connecteurs, templates."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from choregos_contracts import ProjectConfig
from pydantic import Field, field_validator

from .base import Dto, PageMeta


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
    #: An active workflow of the project has a `via: release_train` transition: only then do trains
    #: exist for it, and the console shows them (S30-07).
    has_release_train: bool = False
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


class ConnectorSecretState(Dto):
    """What the console may know of one secret field: where it comes from and when it was last set,
    never its value (ADR 0058)."""

    field: str
    source: Literal["store", "reference"]
    #: only for `source == "reference"`: the reference itself (`env:NAME`) reveals nothing
    reference: str | None = None
    #: only for `source == "store"`: null when the sealed value is gone (a cleared secret)
    version: int | None = None
    rotated_at: datetime | None = None
    rotated_by: str | None = None


class SecretsRewrap(Dto):
    rewrapped: int


class SecretValue(Dto):
    """The body of a secret write: the value, typed once, never returned."""

    value: str = Field(min_length=1, max_length=16384, repr=False)


class ConnectorDto(Dto):
    id: str | None = None
    kind: str
    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    secret_ref: str | None = None
    #: Les secrets, champ par champ, en références (`env:NOM`) — jamais leur valeur (ADR 0034).
    secret_refs: dict[str, str] = Field(default_factory=dict)
    #: One entry per configured secret field: its source and last rotation (ADR 0058).
    secrets: list[ConnectorSecretState] = Field(default_factory=list)
    status: str = "unknown"
    last_check_at: datetime | None = None
    last_error: str | None = None

    @field_validator("secret_refs", mode="before")
    @classmethod
    def _sans_references(cls, valeur: Any) -> Any:
        return valeur or {}


class ConnectorUpsert(Dto):
    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    secret_ref: str | None = None
    secret_refs: dict[str, str] = Field(default_factory=dict)
    #: Secret values typed in the console, field -> value. Write-only: sealed in the database and
    #: replaced by a derived `store:` reference; never returned (ADR 0058).
    secret_values: dict[str, str] = Field(default_factory=dict, repr=False)


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
    #: Ce que le type sait faire (ADR 0034) : `tracker`, `scm`, `identity`, `mcp`…
    capabilities: list[str] = Field(default_factory=list)
    #: Les clefs de configuration qui sont des secrets : elles s'écrivent dans `secret_refs`.
    secret_fields: list[str] = Field(default_factory=list)


class ProjectRequirement(Dto):
    """Une capacité que les workflows du projet exigent, et pourquoi (ADR 0034)."""

    capability: str
    reasons: list[str] = Field(default_factory=list)
    #: le connecteur configuré qui la couvre
    connector: ConnectorDto | None = None
    #: sinon, le type que la plateforme prend par défaut
    default_type: str | None = None


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


# ───────────── connecteurs de l'organisation et politique par opération (ADR 0034) ─────────────

Politique = Literal["allowed", "approval", "forbidden"]


class OperationDto(Dto):
    name: str
    access: str
    policy: str
    #: les groupes de projets qui y ont droit ; aucun : tous les projets de l'organisation
    groups: list[str] = Field(default_factory=list)
    price_usd: float | None = None
    schema_digest: str | None = None
    input_schema: dict[str, Any] | None = None
    description: str | None = None
    #: the identity-provider group whose members approve the actions it proposes (ADR 0052)
    approver_group: str | None = None


class OrgConnectorDto(Dto):
    name: str
    kind: str
    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    secret_refs: dict[str, str] = Field(default_factory=dict)
    secrets: list[ConnectorSecretState] = Field(default_factory=list)
    status: str = "unknown"
    last_check_at: datetime | None = None
    last_error: str | None = None
    created_by: str | None = None
    operations: list[OperationDto] = Field(default_factory=list)

    @field_validator("secret_refs", mode="before")
    @classmethod
    def _sans_references(cls, valeur: Any) -> Any:
        return valeur or {}


class OrgConnectorCreate(Dto):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{1,62}$")
    type: str
    #: la capacité ; par défaut celle du type, s'il n'en déclare qu'une sorte
    kind: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    secret_refs: dict[str, str] = Field(default_factory=dict)
    #: Secret values typed in the console, field -> value: write-only (ADR 0058).
    secret_values: dict[str, str] = Field(default_factory=dict, repr=False)


class OrgConnectorUpdate(Dto):
    """Edit a declared connector. Absent: unchanged. `secret_refs` and `secret_values` MERGE into
    what is there; a stored secret is cleared with its own DELETE."""

    config: dict[str, Any] | None = None
    secret_refs: dict[str, str] | None = None
    secret_values: dict[str, str] | None = Field(default=None, repr=False)


class OperationPatch(Dto):
    policy: Politique | None = None
    groups: list[str] | None = None
    price_usd: float | None = Field(default=None, ge=0)
    #: null (sent) clears it; absent leaves it
    approver_group: str | None = Field(default=None, min_length=1, max_length=200)


class ProjectOperationDto(Dto):
    connector: str
    operation: str
    access: str
    org_policy: str
    project_policy: str | None = None
    effective_policy: str
    description: str | None = None


class ProjectOperationPut(Dto):
    policy: Politique


class ConnectorDiscovery(Dto):
    """Ce que la découverte a changé (ADR 0034) : un outil nouveau naît fermé, un schéma qui dérive
    referme l'opération, un outil retiré par le serveur l'est ici aussi."""

    added: list[str] = Field(default_factory=list)
    changed: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    unchanged: int = 0
