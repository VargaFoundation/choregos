# SPDX-License-Identifier: Apache-2.0
"""Resources of an ontology package (contract 03 §2 and §11): one pydantic model per ``kind``.

The YAML keys are camelCase (``primaryKey``); the Python attributes are snake_case, through aliases.
Unknown keys are refused (``extra="forbid"``): a typo is an ``ONT001``, never a silent default.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

API_VERSION = "choregos.dev/v1alpha1"

Sensitivity = Literal["public", "internal", "confidential", "secret"]
Risk = Literal["low", "medium", "high", "critical"]
Reversibility = Literal["reversible", "compensable", "irreversible"]
Cardinality = Literal["one", "many", "none"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)


class Metadata(_Model):
    name: str
    display_name: str | None = Field(default=None, alias="displayName")
    description: str | None = None
    owner: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    version: str | None = None


class Requirement(_Model):
    name: str
    version: str


class McpDefaults(_Model):
    expose_objects: bool = Field(default=True, alias="exposeObjects")
    expose_actions: bool = Field(default=True, alias="exposeActions")


class Defaults(_Model):
    sensitivity: Sensitivity = "internal"
    mcp: McpDefaults = Field(default_factory=McpDefaults)


class OntologySpec(_Model):
    description: str | None = None
    requires: list[Requirement] = Field(default_factory=list)
    defaults: Defaults = Field(default_factory=Defaults)


class DatasourceSpec(_Model):
    type: Literal["iceberg", "sql", "table", "connector"]
    table: str | None = None
    sql: str | None = None
    connector: str | None = None
    source: str | None = None
    cache: str | None = None
    mapping: dict[str, str] | None = None


class Property(_Model):
    type: str
    required: bool = False
    enum: list[str] | None = None
    format: str | None = None
    sensitivity: Sensitivity | None = None
    description: str | None = None
    unit: str | None = None
    example: Any = None
    searchable: bool = False
    filterable: bool = True
    sortable: bool = True


class Derived(_Model):
    type: str
    expr: str


class SemanticSearch(_Model):
    fields: list[str]
    model: str = "platform/embed"


class Search(_Model):
    semantic: SemanticSearch | None = None


class Freshness(_Model):
    max_age: str = Field(alias="maxAge")


class QualityCheck(_Model):
    name: str
    sql: str


class McpExposure(_Model):
    expose: bool | None = None
    description: str | None = None


class ObjectTypeSpec(_Model):
    datasource: str
    primary_key: str = Field(alias="primaryKey")
    title_property: str | None = Field(default=None, alias="titleProperty")
    properties: dict[str, Property]
    derived: dict[str, Derived] = Field(default_factory=dict)
    search: Search | None = None
    freshness: Freshness | None = None
    quality: list[QualityCheck] = Field(default_factory=list)
    mcp: McpExposure = Field(default_factory=McpExposure)


class LinkEnd(_Model):
    object_type: str = Field(alias="objectType")
    name: str
    cardinality: Literal["one", "many"]


class ForeignKey(_Model):
    side: Literal["from", "to"]
    property: str


class LinkVia(_Model):
    datasource: str
    from_key: str = Field(alias="fromKey")
    to_key: str = Field(alias="toKey")


class LinkTypeSpec(_Model):
    from_: LinkEnd = Field(alias="from")
    to: LinkEnd
    foreign_key: ForeignKey | None = Field(default=None, alias="foreignKey")
    via: LinkVia | None = None


class Target(_Model):
    object_type: str | None = Field(default=None, alias="objectType")
    cardinality: Cardinality


class Precondition(_Model):
    name: str
    expr: str
    message: str | None = None


class Permissions(_Model):
    propose: list[str] = Field(min_length=1)
    approve: str


class Effect(_Model):
    type: str
    with_: dict[str, Any] = Field(default_factory=dict, alias="with")
    timeout: str | None = None
    retries: int | None = None


class Collect(_Model):
    type: str
    with_: dict[str, Any] = Field(default_factory=dict, alias="with")


class Evidence(_Model):
    name: str
    collect: Collect
    expect: str


class Sequencing(_Model):
    mode: Literal["all", "one_at_a_time"] = "all"
    between: list[str] = Field(default_factory=list)


class RateLimit(_Model):
    per_hour: int | None = Field(default=None, alias="perHour")
    per_day: int | None = Field(default=None, alias="perDay")


class ActionTypeSpec(_Model):
    target: Target
    parameters: dict[str, Property] = Field(default_factory=dict)
    preconditions: list[Precondition] = Field(default_factory=list)
    facts_max_age: str | None = Field(default=None, alias="factsMaxAge")
    permissions: Permissions
    risk: Risk
    reversibility: Reversibility
    window: str | None = None
    effects: list[Effect] = Field(min_length=1)
    sequencing: Sequencing | None = None
    compensation: list[Effect] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    on_evidence_failure: Literal["flag", "compensate"] = Field(default="flag", alias="onEvidenceFailure")
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey")
    idempotency_window: str | None = Field(default=None, alias="idempotencyWindow")
    rate_limit: RateLimit | None = Field(default=None, alias="rateLimit")
    mcp: McpExposure = Field(default_factory=McpExposure)


class Approver(_Model):
    role: str | None = None
    group: str | None = None
    resolve: str | None = None
    min: int = Field(default=1, ge=1)


class StepUp(_Model):
    max_age_minutes: int = Field(alias="maxAgeMinutes", ge=1)
    acr: str | None = None


class Sla(_Model):
    remind_after: str | None = Field(default=None, alias="remindAfter")
    escalate_after: str | None = Field(default=None, alias="escalateAfter")
    escalate_to: str | None = Field(default=None, alias="escalateTo")
    expire_after: str | None = Field(default=None, alias="expireAfter")


class Rule(_Model):
    when: str
    approve: Literal["auto"] | None = None
    approvers: list[Approver] = Field(default_factory=list)
    step_up: StepUp | None = Field(default=None, alias="stepUp")
    sla: Sla | None = None


class ApprovalPolicySpec(_Model):
    separation_of_duties: bool = Field(default=True, alias="separationOfDuties")
    rules: list[Rule] = Field(min_length=1)


class WindowSpec(_Model):
    timezone: str = "UTC"
    schedule: list[dict[str, Any]] = Field(default_factory=list)
    freezes: list[dict[str, Any]] = Field(default_factory=list)


class TestExpectation(_Model):
    outcome: Literal["invalid", "pending_approval", "approved"]
    precondition: str | None = None


class OntologyTestSpec(_Model):
    action: str
    fixtures: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    facts: dict[str, dict[str, Any]] = Field(default_factory=dict)
    target: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    expect: TestExpectation


SPECS: dict[str, type[_Model]] = {
    "Ontology": OntologySpec,
    "Datasource": DatasourceSpec,
    "ObjectType": ObjectTypeSpec,
    "LinkType": LinkTypeSpec,
    "ActionType": ActionTypeSpec,
    "ApprovalPolicy": ApprovalPolicySpec,
    "Window": WindowSpec,
    "OntologyTest": OntologyTestSpec,
}
"""The kinds this trial knows. ``Function`` is part of the contract but out of the trial's scope."""
