# SPDX-License-Identifier: Apache-2.0
"""Workflow, politique et modèles d'un projet."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import ConfigDict, Field

from .base import Dto


class WorkflowDefDto(Dto):
    id: str | None = None
    name: str
    version: int
    source: str
    yaml: str
    json_doc: dict[str, Any] | None = Field(default=None, alias="json")
    checksum: str
    is_active: bool = True
    is_default: bool = False
    created_by: str | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(extra="forbid", from_attributes=True, populate_by_name=True)


class WorkflowPut(Dto):
    yaml: str
    source: Literal["repo", "platform", "template"] = "platform"
    activate: bool = True
    #: La version active que l'éditeur a lue : si une autre a été publiée depuis, 409.
    base_version: int | None = None
    #: Créer, pas republier : un workflow de ce nom, actif ou non, répond 409 (#336).
    create_only: bool = False


class WorkflowSummaryDto(Dto):
    name: str
    version: int
    description: str | None = None
    is_default: bool = False
    open_items: int = 0
    created_by: str | None = None
    updated_at: datetime | None = None


class RoutingCondition(Dto):
    labels_any: list[str] = Field(default_factory=list)
    labels_all: list[str] = Field(default_factory=list)
    item_type: str | None = None


class RoutingRule(Dto):
    when: RoutingCondition
    workflow: str


class WorkflowRoutingDto(Dto):
    default: str
    rules: list[RoutingRule] = Field(default_factory=list)


class WorkflowValidateRequest(Dto):
    yaml: str | None = None
    json_doc: dict[str, Any] | None = Field(default=None, alias="json")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class WorkflowIssue(Dto):
    code: str
    message: str
    path: str | None = None
    line: int | None = None
    column: int | None = None
    #: The former spelling of a code renamed to English, for one release (ADR 0046).
    deprecated_code: str | None = None


class WorkflowValidation(Dto):
    valid: bool
    errors: list[WorkflowIssue] = Field(default_factory=list)
    warnings: list[WorkflowIssue] = Field(default_factory=list)
    graph: dict[str, Any] | None = None
    #: La vue « processus » : chaque transition dite en clair (ADR 0031).
    process: list[dict[str, Any]] = Field(default_factory=list)


class WorkflowEditRequest(Dto):
    yaml: str
    #: Validées par `choregos_core.dsl.edition.OPERATIONS` : un refus dit laquelle et pourquoi.
    operations: list[dict[str, Any]] = Field(min_length=1)


class WorkflowEditResult(WorkflowValidation):
    yaml: str
    diff: str
    inverse: list[dict[str, Any]] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)


class WorkflowTemplateDto(Dto):
    name: str
    version: int
    display: str
    description: str = ""
    yaml: str


class PolicyDto(Dto):
    id: str | None = None
    name: str
    version: int
    yaml: str
    json_doc: dict[str, Any] | None = Field(default=None, alias="json")
    is_active: bool = True
    #: What the organisation's ceiling brings down in this policy (S27-05): the policy that runs is
    #: the clamped one. Empty when it is within the ceiling.
    clamps: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", from_attributes=True, populate_by_name=True)


class PolicyCeilingDto(Dto):
    """An organisation's policy ceiling (S27-05): the caps every project policy stays under."""

    ceiling: dict[str, Any] | None = None
    updated_by: str | None = None
    updated_at: str | None = None


class PolicyCeilingPut(Dto):
    #: `budgets`, `attempts`, `scope`, `findings` caps; `null` removes the ceiling.
    ceiling: dict[str, Any] | None = None


class PolicyPut(Dto):
    yaml: str
    activate: bool = True


class GatewayModelDto(Dto):
    model_name: str
    litellm_model: str
    provider: str = ""
    input_cost_per_1k: float | None = None
    output_cost_per_1k: float | None = None
    max_input_tokens: int | None = None
    supports_tool_calling: bool = True
    supports_vision: bool = False
    internal_price: bool = False
    source: Literal["gateway", "manual"] = "gateway"
    owned_by: str = ""


class ProjectModelProfile(Dto):
    litellm_model: str
    params: dict[str, Any] = Field(default_factory=dict)
    max_turns_factor: float = 1.0


class ProjectModels(Dto):
    profiles: dict[str, ProjectModelProfile] = Field(default_factory=dict)
    allow_unvalidated: bool = False
    inherited: dict[str, str] = Field(default_factory=dict)


class ModelMatrixEntry(Dto):
    backend: str
    model: str
    validated: bool
    success_rate: float | None = None
    median_cost_usd: float | None = None
    median_duration_s: float | None = None
    with_memory: bool | None = None
    report_url: str | None = None
    # S37-09 (ADR 0055): the cell's context reduction, and its tokens from the gateway's ledger.
    context: Literal["none", "rtk", "headroom", "rtk+headroom"] | None = None
    metered: int | None = None
    median_tokens_in: float | None = None
    median_tokens_cached: float | None = None
    median_tokens_out: float | None = None
    median_turns: float | None = None


class ModelMatrix(Dto):
    generated_at: datetime | None = None
    entries: list[ModelMatrixEntry] = Field(default_factory=list)


# ───────────── the organisation's model catalogue (S39-07b, ADR 0060) ─────────────

Usage = Literal["agent", "embed", "distill", "eval"]


def _agent_usage() -> list[Usage]:
    return ["agent"]


class PriceOverride(Dto):
    input_per_1k: float = Field(ge=0)
    output_per_1k: float = Field(ge=0)


class ModelGatewayFacts(Dto):
    provider: str = ""
    upstream_model: str = ""
    input_cost_per_1k: float | None = None
    output_cost_per_1k: float | None = None
    max_input_tokens: int | None = None
    supports_tool_calling: bool = True
    supports_vision: bool = False


class ModelCatalogueEntry(Dto):
    id: str
    model_name: str
    display_name: str
    source: Literal["gateway", "manual"]
    enabled: bool
    allowed_roles: list[str] = Field(default_factory=list)
    usages: list[Usage] = Field(default_factory=list)
    price_override: PriceOverride | None = None
    gateway: ModelGatewayFacts | None = None
    last_seen_at: datetime | None = None
    missing_since: datetime | None = None
    notes: str = ""
    updated_by: str | None = None
    updated_at: datetime | None = None


class ModelCatalogueEntryCreate(Dto):
    model_name: str = Field(min_length=1, max_length=200)
    display_name: str | None = Field(default=None, max_length=200)
    enabled: bool = True
    allowed_roles: list[str] = Field(default_factory=list)
    usages: list[Usage] = Field(default_factory=_agent_usage)
    price_override: PriceOverride | None = None
    notes: str = ""


class ModelCataloguePatch(Dto):
    display_name: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None
    allowed_roles: list[str] | None = None
    usages: list[Usage] | None = None
    #: `null` is "no change"; to remove an override send `clear_price_override: true`.
    price_override: PriceOverride | None = None
    clear_price_override: bool = False
    notes: str | None = None


class ModelTiers(Dto):
    cheap: str | None = None
    standard: str | None = None
    strong: str | None = None
    aux: str | None = None
    embed: str | None = None
    local: str | None = None


class ModelCatalogueGateway(Dto):
    type: str
    capabilities: list[str] = Field(default_factory=list)


class ModelCatalogue(Dto):
    mode: Literal["open", "curated"]
    gateway: ModelCatalogueGateway
    tiers: ModelTiers
    entries: list[ModelCatalogueEntry]


class ModelCatalogueSettings(Dto):
    mode: Literal["open", "curated"]
    tiers: ModelTiers = Field(default_factory=ModelTiers)


class ModelCurationRefusal(Dto):
    kind: Literal["agent", "project_profile"]
    ref: str
    model: str


class ModelCuratePreview(Dto):
    refused: list[ModelCurationRefusal] = Field(default_factory=list)


class ModelSyncResult(Dto):
    added: int
    missing: int
    seen: int


class UsableModel(Dto):
    model_name: str
    display_name: str
    provider: str = ""
    input_cost_per_1k: float | None = None
    output_cost_per_1k: float | None = None
    priced_by: Literal["gateway", "override", "none"] = "none"
    tiers: list[str] = Field(default_factory=list)
