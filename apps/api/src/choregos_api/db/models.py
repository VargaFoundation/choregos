# SPDX-License-Identifier: Apache-2.0
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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, Json, PkMixin, TimestampMixin, uuid7


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
    #: Le nom du workflow par défaut (ADR 0031) : celui d'un ticket qui ne dit pas le sien et
    #: qu'aucune règle de routage ne désigne. Le défaut appartient au projet, pas à une version.
    default_workflow: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Les règles qui choisissent le workflow d'un ticket venu d'un tracker (ADR 0031) : une liste
    #: ordonnée de `{when: {labels_any, labels_all, item_type}, workflow}`. Hors de `config`, que
    #: `PATCH /projects/{id}` remplace en entier.
    workflow_routing: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list)

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
    __table_args__ = (
        UniqueConstraint("project_id", "name", "version", name="project_name_version"),
        # ADR 0031 : un projet porte plusieurs workflows, mais une seule version active par nom.
        Index(
            "uq_workflow_defs_actif_par_nom",
            "project_id",
            "name",
            unique=True,
            sqlite_where=text("is_active"),
            postgresql_where=text("is_active"),
        ),
    )

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String(16), default="platform")
    yaml: Mapped[str] = mapped_column(Text)
    json_doc: Mapped[dict[str, Any]] = mapped_column("json", Json, default=dict)
    checksum: Mapped[str] = mapped_column(String(80), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    #: Qui a publié cette version : l'historique des versions le montre (ADR 0031).
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)


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
    #: La VERSION du workflow où le ticket est né (ADR 0031) : publier une version nouvelle ne le
    #: déplace jamais ; seul `migrate` le fait, exprès.
    workflow_def_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_defs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    policy_id: Mapped[str | None] = mapped_column(
        ForeignKey("policies.id", ondelete="SET NULL"), nullable=True
    )
    temporal_wf_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Pourquoi l'interpréteur du ticket est mort (`{message, activity, at}`), ou rien.
    #: Posé par le workflow lui-même en mourant, effacé quand il redémarre.
    failure: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    allowed_paths: Mapped[list[str]] = mapped_column(Json, default=list)
    #: Les champs du ticket, validés à sa naissance par `metadata.inputs` de son workflow (ADR 0031).
    fields: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    pr_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    totals: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    documents: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)  # spec_markdown, plan_markdown…

    runs: Mapped[list[Run]] = relationship(back_populates="work_item", cascade="all, delete-orphan")


class Run(Base, PkMixin, TimestampMixin):
    __tablename__ = "runs"
    __table_args__ = (Index("ix_runs_project_status", "project_id", "status"),)

    # L'identifiant d'un run N'EST PAS un UUID : il est déterministe et lisible,
    # `<ticket>-<transition>-<tentative>` (activities/stage.py), et c'est lui qui rend
    # l'étape rejouable sans créer un second run. Les 36 caractères hérités de `PkMixin`
    # ne suffisent pas — PostgreSQL répond « value too long for type character
    # varying(36) », là où SQLite, qui n'applique pas les longueurs, ne dit rien.
    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=uuid7)
    work_item_id: Mapped[str] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    transition_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stage_role: Mapped[str] = mapped_column(String(32), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    actor: Mapped[str | None] = mapped_column(String(64), nullable=True)
    backend: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    executor_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: L'agent du registre qui a fait ce run, et sa version (ADR 0033) ; vide pour un playbook.
    agent_slug: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    agent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    executor_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tokens: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    # La dépense d'un run n'est lue qu'une fois au gateway : ce drapeau interdit le double comptage.
    spend_collected: Mapped[bool] = mapped_column(Boolean, default=False)
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
    # `model` ou `tool` : la même ligne porte ce qu'a coûté un appel de modèle et ce qu'a
    # coûté un appel d'outil du catalogue. Sans cette colonne, les agrégats par modèle
    # compteraient les outils comme des modèles.
    kind: Mapped[str] = mapped_column(String(16), default="model", index=True)
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
    """Journal d'audit. `org_id` est NULL pour un événement de plateforme (une connexion, un
    jeton) : ces lignes ne sont visibles que d'une session de portée `*`. Sans cette colonne,
    la table était hors RLS et la route ne filtrait rien — un `project_owner` d'une
    organisation lisait l'audit de toutes les autres."""

    __tablename__ = "audit_log"

    actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    actor_kind: Mapped[str] = mapped_column(String(16), default="user")
    org_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
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
    #: Un jeton MCP peut être lié à un projet : il ne voit alors que lui (ADR 0030).
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    #: Le client du dernier appel (`User-Agent`, tronqué) : la page Integrations dit « connecté ».
    last_client: Mapped[str | None] = mapped_column(String(200), nullable=True)


class MemoryFact(Base, PkMixin, TimestampMixin):
    """Repli lexical : mémoire stockée dans Choregos quand Ecphoria n'est pas déployé.

    `embedding` est un `Json`, pas un vecteur : la similarité est un produit scalaire entre
    sacs de mots, calculé en Python. Le connecteur s'appelait `pgvector`, ce qui promettait
    autre chose."""

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
    # Même longueur que `runs.id` : cette colonne le recopie sans clé étrangère (la clé
    # survit au run qu'elle plafonnait).
    run_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    budget_usd: Mapped[float] = mapped_column(Float, default=0.0)
    spend_usd: Mapped[float] = mapped_column(Float, default=0.0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


# ───────────────────────────── le registre d'agents (ADR 0033) ─────────────────────────────


class Agent(Base, PkMixin, TimestampMixin):
    """Un agent de l'organisation : interne (la plateforme le fait tourner) ou externe (un client
    de la porte MCP). Ce qu'il EST vit dans ses versions, immuables ; ici, son identité et son
    état — actif, suspendu, révoqué, expiré."""

    __tablename__ = "agents"
    __table_args__ = (UniqueConstraint("org_id", "slug", name="uq_agents_org_slug"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16), default="internal")
    display_name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentVersion(Base, PkMixin, TimestampMixin):
    """Une version d'agent — instructions, modèle, limites, skills, serveurs MCP, budget. Elle ne se
    modifie jamais : un changement publie la suivante."""

    __tablename__ = "agent_versions"
    __table_args__ = (UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_version"),)

    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    spec: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    checksum: Mapped[str] = mapped_column(String(80))
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)


class ProjectAgent(Base, PkMixin, TimestampMixin):
    """L'épingle d'un projet sur une version d'agent, et ses surcharges — qui ne font que resserrer."""

    __tablename__ = "project_agents"
    __table_args__ = (UniqueConstraint("project_id", "agent_id", name="uq_project_agents_project_agent"),)

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    overrides: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)


# ───────────────────────────── la bibliothèque de skills (ADR 0033) ─────────────────────────────


class Skill(Base, PkMixin, TimestampMixin):
    """Une skill de l'organisation : un dossier (`SKILL.md` et ses fichiers) qu'un agent porte. Ce
    qu'elle contient vit dans ses versions, immuables."""

    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("org_id", "slug", name="uq_skills_org_slug"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")


class SkillVersion(Base, PkMixin, TimestampMixin):
    """Les fichiers d'une version de skill — 64 au plus, 512 Kio en tout — et leur empreinte, que le
    runner vérifie avant de les poser."""

    __tablename__ = "skill_versions"
    __table_args__ = (UniqueConstraint("skill_id", "version", name="uq_skill_versions_skill_version"),)

    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"), index=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    files: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    digest: Mapped[str] = mapped_column(String(80))
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)


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
    Agent,
    AgentVersion,
    ProjectAgent,
    Skill,
    SkillVersion,
]


def objet_du_coeur(objet: Any, nom: str | None, type_: str, reflete: bool, compare_a: Any) -> bool:
    """Filtre `include_object` d'Alembic : les migrations du cœur ne décrivent que SES tables.

    Un greffon inscrit ses modèles dans le même `Base` — c'est ce qui les met dans `create_all()` —
    et crée ses tables par sa propre branche de migrations (`choregos.migrations`). Sans ce filtre,
    une autogénération lancée avec un greffon installé ferait entrer ses tables dans une migration
    du cœur, et la comparaison modèles/migrations du cœur les verrait comme manquantes. Une table
    est au cœur si sa classe est définie dans `choregos_api` ; celles d'un greffon ne le sont pas.
    """
    if type_ != "table":
        return True
    return nom in {
        mapper.class_.__tablename__
        for mapper in Base.registry.mappers
        if mapper.class_.__module__.startswith("choregos_api.")
    }
