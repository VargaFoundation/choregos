"""Projets, provisioning, connecteurs, templates."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from choregos_contracts import ProjectConfig
from pydantic import Field

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


class ConnectorDto(Dto):
    id: str | None = None
    kind: str
    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    secret_ref: str | None = None
    status: str = "unknown"
    last_check_at: datetime | None = None
    last_error: str | None = None


class ConnectorUpsert(Dto):
    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    secret_ref: str | None = None


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
