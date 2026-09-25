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

    model_config = ConfigDict(extra="forbid", from_attributes=True, populate_by_name=True)


class WorkflowPut(Dto):
    yaml: str
    source: Literal["repo", "platform", "template"] = "platform"
    activate: bool = True


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


class WorkflowValidation(Dto):
    valid: bool
    errors: list[WorkflowIssue] = Field(default_factory=list)
    warnings: list[WorkflowIssue] = Field(default_factory=list)
    graph: dict[str, Any] | None = None


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

    model_config = ConfigDict(extra="forbid", from_attributes=True, populate_by_name=True)


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


class ModelMatrix(Dto):
    generated_at: datetime | None = None
    entries: list[ModelMatrixEntry] = Field(default_factory=list)
