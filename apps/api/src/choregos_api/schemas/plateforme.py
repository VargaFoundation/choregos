"""Backends, exécuteurs, clés, audit, webhooks."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from .base import Dto, PageMeta


class ConformanceReport(Dto):
    last_run_at: datetime | None = None
    passed: int | None = None
    total: int | None = None
    report_url: str | None = None
    failures: list[str] = Field(default_factory=list)


class AgentBackendInfo(Dto):
    name: str
    enabled: bool
    version: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    conformance: ConformanceReport = Field(default_factory=ConformanceReport)
    disabled_reason: str | None = None


class AgentBackendUpdate(Dto):
    name: str
    enabled: bool
    disabled_reason: str | None = None


class ExecutorInfo(Dto):
    kind: Literal["tekton", "k8s_job", "local_docker", "aca"]
    enabled: bool = True
    cluster: str | None = None
    namespace_pattern: str | None = None
    runner_image: str | None = None
    default_timeout_minutes: int = 120


class GatewayKeyInfo(Dto):
    key_id: str
    run_id: str | None = None
    project_slug: str | None = None
    budget_usd: float | None = None
    spend_usd: float = 0.0
    expires_at: datetime | None = None
    revoked: bool = False
    created_at: datetime


class AuditEntry(Dto):
    id: str
    actor_id: str | None = None
    actor_kind: str
    action: str
    target_type: str | None = None
    target_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: datetime


class AuditPage(Dto):
    items: list[AuditEntry]
    meta: PageMeta


class WebhookAck(Dto):
    accepted: bool
    duplicate: bool = False
    events: int = 0
