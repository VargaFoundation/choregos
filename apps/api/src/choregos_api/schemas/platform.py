# SPDX-License-Identifier: Apache-2.0
"""Backends, executors, keys, audit, webhooks."""

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
    kind: Literal["tekton", "k8s_job", "local_docker", "aca", "agent_sandbox"]
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


class PlatformIdentityRow(Dto):
    key: str
    label: str
    #: Display value; a secret shows only `set` or `missing`.
    value: str
    #: `settings`: set by the deployment; `derived`: computed from other settings; `default`: built in.
    source: Literal["settings", "derived", "default"]


class PlatformIdentity(Dto):
    items: list[PlatformIdentityRow]


class RelinkWindow(Dto):
    """The re-link window of a change of identity provider (ADR 0160)."""

    #: `open` until `until`; `closed` once `until` passed; `none` when the deployment sets no window.
    state: Literal["open", "closed", "none"]
    until: datetime | None = None
    #: The plan's issuer is the configured one: the window applies to the sign-ins it describes.
    applies: bool


class RelinkCounts(Dto):
    pending: int
    ambiguous: int
    current: int
    unbound: int
    not_concerned: int


class RelinkAccount(Dto):
    user_id: str
    email: str
    display_name: str
    #: The issuer of the account's subject, null when it was bound before issuers were recorded.
    issuer: str | None = None
    last_login_at: datetime | None = None
    #: For an ambiguous account: the other accounts' addresses that are the same, letter case aside.
    same_address_as: list[str] = Field(default_factory=list)


class RelinkPlan(Dto):
    """What a re-link window for `issuer` does to each account: a dry run, nothing is changed."""

    issuer: str
    configured_issuer: str
    window: RelinkWindow
    counts: RelinkCounts
    pending: list[RelinkAccount]
    ambiguous: list[RelinkAccount]
    generated_at: datetime
