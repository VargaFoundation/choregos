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
    #: An opaque digest of the name and configuration, to send back as `base_revision` (S47-06).
    config_revision: str | None = None
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
    #: The `config_revision` the client read. When it is no longer current, the PATCH answers 409
    #: `stale_base` and writes nothing; absent, the last write wins (S47-06).
    base_revision: str | None = None


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
    #: The secrets, field by field, as references (`env:NAME`), never their value (ADR 0034).
    secret_refs: dict[str, str] = Field(default_factory=dict)
    #: One entry per configured secret field: its source and last rotation (ADR 0058).
    secrets: list[ConnectorSecretState] = Field(default_factory=list)
    status: str = "unknown"
    last_check_at: datetime | None = None
    last_error: str | None = None

    @field_validator("secret_refs", mode="before")
    @classmethod
    def _without_references(cls, value: Any) -> Any:
        return value or {}


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
    #: What the type can do (ADR 0034): `tracker`, `scm`, `identity`, `mcp`...
    capabilities: list[str] = Field(default_factory=list)
    #: The configuration keys that are secrets: they are written in `secret_refs`.
    secret_fields: list[str] = Field(default_factory=list)
    #: The secret fields that take several keys, one per slot (`api_key.2` …, ADR 0089).
    pooled_fields: list[str] = Field(default_factory=list)


class ProjectRequirement(Dto):
    """A capability that the project's workflows require, and why (ADR 0034)."""

    capability: str
    reasons: list[str] = Field(default_factory=list)
    #: the configured connector that covers it
    connector: ConnectorDto | None = None
    #: otherwise, the type the platform takes by default
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


# ───────────── organisation connectors and policy per operation (ADR 0034) ─────────────

OperationPolicy = Literal["allowed", "approval", "forbidden"]
#: An argument the platform fills, never the agent: the list a plugin server's operation declares
#: (`plugin-server.schema.json`), and the one an organisation's rule states for a discovered tool.
PlatformArgument = Literal["action_key", "work_item_key"]


class OperationDto(Dto):
    name: str
    access: str
    policy: str
    #: the project groups entitled to it; none: every project of the organisation
    groups: list[str] = Field(default_factory=list)
    price_usd: float | None = None
    schema_digest: str | None = None
    input_schema: dict[str, Any] | None = None
    description: str | None = None
    #: the identity-provider group whose members approve the actions it proposes (ADR 0052)
    approver_group: str | None = None
    #: the most one result may weigh when it reaches the agent; none: the platform's default (S40-17)
    result_max_bytes: int | None = None
    #: a JSON Schema every call's arguments must also satisfy, whichever agent calls; `{project}` is the
    #: run's project slug (S60-06, ADR 0128)
    arguments: dict[str, Any] | None = None
    #: the arguments the organisation's rule says the platform fills, for a discovered tool; listing
    #: `action_key` for a write through an outpost says it takes the idempotency key (ADR 0149 §6)
    platform_arguments: list[PlatformArgument] = Field(default_factory=list)
    #: an in-doubt outcome is accepted for this write through an outpost, explicitly (ADR 0149 §6)
    in_doubt_accepted: bool = False

    @field_validator("platform_arguments", mode="before")
    @classmethod
    def _none_stated(cls, value: Any) -> Any:
        return value or []


class OrgConnectorServer(Dto):
    """What the console needs to configure a plugin's server (ADR 0096, contract-change S43-08)."""

    plugin: str
    version: str
    server: str
    config_schema: dict[str, Any] = Field(default_factory=dict)
    secret_fields: list[str] = Field(default_factory=list)


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
    catalogue_entry: str | None = None
    operations: list[OperationDto] = Field(default_factory=list)
    availability: Literal["all_projects", "selected_projects"] = "all_projects"
    #: the projects it is on for, among those the caller sees; empty with `all_projects`
    enabled_projects: list[str] = Field(default_factory=list)
    #: the plugin server it runs: its configuration is the server's schema, not the `mcp` type's
    server: OrgConnectorServer | None = None

    @field_validator("secret_refs", mode="before")
    @classmethod
    def _without_references(cls, value: Any) -> Any:
        return value or {}


class OrgConnectorCreate(Dto):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{1,62}$")
    type: str
    #: the capability; by default the type's, if it declares only one kind
    kind: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    secret_refs: dict[str, str] = Field(default_factory=dict)
    #: Secret values typed in the console, field -> value: write-only (ADR 0058).
    secret_values: dict[str, str] = Field(default_factory=dict, repr=False)
    availability: Literal["all_projects", "selected_projects"] = "all_projects"
    #: Open the connector's hosts in the organisation's egress allowlist in the same call (S39-22):
    #: without it a missing host is a 409 `egress_required`. Needs `tools:grant`.
    allow_egress: bool = False


class OrgConnectorUpdate(Dto):
    """Edit a declared connector. Absent: unchanged. `secret_refs` and `secret_values` MERGE into
    what is there; a stored secret is cleared with its own DELETE."""

    config: dict[str, Any] | None = None
    secret_refs: dict[str, str] | None = None
    secret_values: dict[str, str] | None = Field(default=None, repr=False)
    #: the organisation's administrator only (ADR 0074)
    availability: Literal["all_projects", "selected_projects"] | None = None
    allow_egress: bool = False


class ProjectOrgConnectorDto(Dto):
    """An organisation connector, as a project sees it: is it on here, may the caller switch it."""

    name: str
    kind: str
    type: str
    availability: Literal["all_projects", "selected_projects"]
    enabled: bool
    can_toggle: bool
    cannot_reason: str | None = None


class OperationPatch(Dto):
    policy: OperationPolicy | None = None
    groups: list[str] | None = None
    price_usd: float | None = Field(default=None, ge=0)
    #: null (sent) clears it; absent leaves it
    approver_group: str | None = Field(default=None, min_length=1, max_length=200)
    #: null (sent) clears it; absent leaves it; above the platform's ceiling is refused (S40-17)
    result_max_bytes: int | None = Field(default=None, ge=1024)
    #: null (sent) clears it; absent leaves it; refused when it is not a schema the platform reads or
    #: names an argument the operation does not have (S60-06, ADR 0128)
    arguments: dict[str, Any] | None = None
    #: null or empty (sent) clears them; absent leaves them (S65-14, ADR 0149 §6)
    platform_arguments: list[PlatformArgument] | None = None
    #: null or false (sent) withdraws it; absent leaves it (S65-14, ADR 0149 §6)
    in_doubt_accepted: bool | None = None

    @field_validator("platform_arguments")
    @classmethod
    def _each_once(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("a platform argument is listed once")
        return value


class ProjectOperationDto(Dto):
    connector: str
    operation: str
    access: str
    org_policy: str
    project_policy: str | None = None
    effective_policy: str
    description: str | None = None
    #: what the organisation binds of the call's arguments, and what this project binds on top (S60-06)
    org_arguments: dict[str, Any] | None = None
    project_arguments: dict[str, Any] | None = None


class ProjectOperationPut(Dto):
    policy: OperationPolicy
    #: binds the call's arguments on top of the organisation's; absent clears (S60-06, ADR 0128)
    arguments: dict[str, Any] | None = None


class ConnectorDiscovery(Dto):
    """What discovery changed (ADR 0034): a new tool is born closed, a schema that drifts
    closes the operation again, a tool the server removed is removed here too."""

    added: list[str] = Field(default_factory=list)
    changed: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    unchanged: int = 0
