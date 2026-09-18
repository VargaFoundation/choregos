"""Modèle de données (docs/plan/01 §1.3).

Toutes les tables porteuses de données projet ont `project_id` et sont couvertes par
la RLS PostgreSQL (`SET app.current_org`), posée par la migration initiale.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, Json, PkMixin, TimestampMixin


class Organization(Base, PkMixin, TimestampMixin):
    __tablename__ = "organizations"

    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    settings: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)

    projects: Mapped[list[Project]] = relationship(back_populates="org", cascade="all, delete-orphan")


class User(Base, PkMixin, TimestampMixin):
    __tablename__ = "users"

    oidc_sub: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_service: Mapped[bool] = mapped_column(Boolean, default=False)


class Membership(Base, PkMixin, TimestampMixin):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "org_id", "project_id", name="user_org_project"),)

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    role: Mapped[str] = mapped_column(String(32))

    user: Mapped[User] = relationship(lazy="joined")


class Project(Base, PkMixin, TimestampMixin):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("org_id", "slug", name="org_slug"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(200))
    template_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    provision_state: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)

    org: Mapped[Organization] = relationship(back_populates="projects")
    connectors: Mapped[list[Connector]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Connector(Base, PkMixin, TimestampMixin):
    __tablename__ = "connectors"
    __table_args__ = (UniqueConstraint("project_id", "kind", name="project_kind"),)

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    type: Mapped[str] = mapped_column(String(64))
    config: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="unknown")
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped[Project] = relationship(back_populates="connectors")


class WorkflowDef(Base, PkMixin, TimestampMixin):
    __tablename__ = "workflow_defs"
    __table_args__ = (UniqueConstraint("project_id", "name", "version", name="project_name_version"),)

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String(16), default="platform")
    yaml: Mapped[str] = mapped_column(Text)
    json_doc: Mapped[dict[str, Any]] = mapped_column("json", Json, default=dict)
    checksum: Mapped[str] = mapped_column(String(80), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class PolicyDef(Base, PkMixin, TimestampMixin):
    __tablename__ = "policies"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64), default="default")
    version: Mapped[int] = mapped_column(Integer, default=1)
    yaml: Mapped[str] = mapped_column(Text)
    json_doc: Mapped[dict[str, Any]] = mapped_column("json", Json, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class ModelProfileRow(Base, PkMixin, TimestampMixin):
    __tablename__ = "model_profiles"

    scope: Mapped[str] = mapped_column(String(16), default="project")
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(64), index=True)
    litellm_model: Mapped[str] = mapped_column(String(200))
    params: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    validated_backends: Mapped[list[str]] = mapped_column(Json, default=list)


class Template(Base, PkMixin, TimestampMixin):
    __tablename__ = "templates"
    __table_args__ = (UniqueConstraint("name", "version", name="template_name_version"),)

    name: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[str] = mapped_column(String(32))
    manifest: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    repo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)


class WorkItem(Base, PkMixin, TimestampMixin):
    __tablename__ = "work_items"
    __table_args__ = (
        UniqueConstraint("project_id", "tracker_key", name="project_tracker_key"),
        Index("ix_work_items_project_state", "project_id", "state"),
    )

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    tracker_key: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(String(500))
    body_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    size: Mapped[str | None] = mapped_column(String(4), nullable=True)
    risk: Mapped[str | None] = mapped_column(String(8), nullable=True)
    state: Mapped[str] = mapped_column(String(64), default="inbox", index=True)
    workflow_def_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_defs.id", ondelete="SET NULL"), nullable=True
    )
    policy_id: Mapped[str | None] = mapped_column(
        ForeignKey("policies.id", ondelete="SET NULL"), nullable=True
    )
    temporal_wf_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    allowed_paths: Mapped[list[str]] = mapped_column(Json, default=list)
    pr_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    totals: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    documents: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)  # spec_markdown, plan_markdown…

    runs: Mapped[list[Run]] = relationship(back_populates="work_item", cascade="all, delete-orphan")


class Run(Base, PkMixin, TimestampMixin):
    __tablename__ = "runs"
    __table_args__ = (Index("ix_runs_project_status", "project_id", "status"),)

    work_item_id: Mapped[str] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    transition_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stage_role: Mapped[str] = mapped_column(String(32), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    actor: Mapped[str | None] = mapped_column(String(64), nullable=True)
    backend: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    executor_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    executor_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tokens: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    gateway_key_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    stage_input: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    transcript_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    context_pack_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    context_pack: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    playbook_checksum: Mapped[str | None] = mapped_column(String(80), nullable=True)
    allowed_paths: Mapped[list[str]] = mapped_column(Json, default=list)

    work_item: Mapped[WorkItem] = relationship(back_populates="runs")
    events: Mapped[list[RunEvent]] = relationship(back_populates="run", cascade="all, delete-orphan")


class RunEvent(Base, PkMixin):
    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="run_seq"),)

    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    run: Mapped[Run] = relationship(back_populates="events")


class HumanRequest(Base, PkMixin, TimestampMixin):
    __tablename__ = "human_requests"

    work_item_id: Mapped[str] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    transition_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)


class Event(Base, PkMixin):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_project_ts", "project_id", "ts"),)

    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    work_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("work_items.id", ondelete="CASCADE"), nullable=True, index=True
    )
    type: Mapped[str] = mapped_column(String(80), index=True)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Finding(Base, PkMixin, TimestampMixin):
    __tablename__ = "findings"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    origin_work_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("work_items.id", ondelete="SET NULL"), nullable=True
    )
    origin_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(300))
    type: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16), index=True)
    evidence: Mapped[str] = mapped_column(Text)
    suggested_fix: Mapped[str | None] = mapped_column(Text, nullable=True)
    estimate: Mapped[str | None] = mapped_column(String(4), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_work_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("work_items.id", ondelete="SET NULL"), nullable=True
    )
    duplicate_of: Mapped[str | None] = mapped_column(String(36), nullable=True)
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    relevant: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    embedding: Mapped[list[str] | None] = mapped_column(Json, nullable=True)


class Release(Base, PkMixin, TimestampMixin):
    __tablename__ = "releases"
    __table_args__ = (Index("ix_releases_project_env", "project_id", "env"),)

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    env: Mapped[str] = mapped_column(String(32))
    batch_no: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="collecting", index=True)
    items: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    verdict: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    promotion_url: Mapped[str | None] = mapped_column(String(500), nullable=True)


class Deployment(Base, PkMixin, TimestampMixin):
    __tablename__ = "deployments"

    release_id: Mapped[str] = mapped_column(ForeignKey("releases.id", ondelete="CASCADE"), index=True)
    env: Mapped[str] = mapped_column(String(32))
    revision: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cd_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    analysis: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)


class CostLedger(Base, PkMixin):
    __tablename__ = "cost_ledger"
    __table_args__ = (Index("ix_cost_ledger_project_ts", "project_id", "ts"),)

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    work_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("work_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(64), default="")
    model: Mapped[str] = mapped_column(String(200), default="")
    backend: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stage_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    size: Mapped[str | None] = mapped_column(String(4), nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    tokens_cached: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cost_eur: Mapped[float] = mapped_column(Float, default=0.0)
    fx_rate: Mapped[float] = mapped_column(Float, default=1.0)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class AuditLog(Base, PkMixin):
    __tablename__ = "audit_log"

    actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    actor_kind: Mapped[str] = mapped_column(String(16), default="user")
    action: Mapped[str] = mapped_column(String(80), index=True)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ApiToken(Base, PkMixin, TimestampMixin):
    __tablename__ = "api_tokens"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    scopes: Mapped[list[str]] = mapped_column(Json, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MemoryFact(Base, PkMixin, TimestampMixin):
    """Repli pgvector : mémoire stockée dans Choregos quand Ecphoria n'est pas déployé."""

    __tablename__ = "memory_facts"
    __table_args__ = (Index("ix_memory_facts_project_subject", "project_id", "subject"),)

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    subject: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provenance: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    proposed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    embedding: Mapped[list[str] | None] = mapped_column(Json, nullable=True)
    paths: Mapped[list[str]] = mapped_column(Json, default=list)


class WebhookDelivery(Base, PkMixin):
    """Déduplication des webhooks : une livraison traitée une seule fois."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (UniqueConstraint("source", "delivery_id", name="source_delivery"),)

    source: Mapped[str] = mapped_column(String(32), index=True)
    delivery_id: Mapped[str] = mapped_column(String(255))
    event_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)


class BackendRegistryRow(Base, PkMixin, TimestampMixin):
    """État des backends ACP : activés, résultats de conformité (S2-11)."""

    __tablename__ = "agent_backends"

    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    capabilities: Mapped[list[str]] = mapped_column(Json, default=list)
    conformance: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    disabled_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class ExecutorRow(Base, PkMixin, TimestampMixin):
    __tablename__ = "executors"

    kind: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    cluster: Mapped[str | None] = mapped_column(String(128), nullable=True)
    namespace_pattern: Mapped[str | None] = mapped_column(String(128), nullable=True)
    runner_image: Mapped[str | None] = mapped_column(String(255), nullable=True)
    default_timeout_minutes: Mapped[int] = mapped_column(Integer, default=120)


class GatewayKeyRow(Base, PkMixin, TimestampMixin):
    __tablename__ = "gateway_keys"

    key_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    budget_usd: Mapped[float] = mapped_column(Float, default=0.0)
    spend_usd: Mapped[float] = mapped_column(Float, default=0.0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


ALL_TABLES = [
    Organization,
    User,
    Membership,
    Project,
    Connector,
    WorkflowDef,
    PolicyDef,
    ModelProfileRow,
    Template,
    WorkItem,
    Run,
    RunEvent,
    HumanRequest,
    Event,
    Finding,
    Release,
    Deployment,
    CostLedger,
    AuditLog,
    ApiToken,
    MemoryFact,
    WebhookDelivery,
    BackendRegistryRow,
    ExecutorRow,
    GatewayKeyRow,
]
