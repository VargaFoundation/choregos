# SPDX-License-Identifier: Apache-2.0
"""Modèle de données (docs/plan/01 §1.3).

Toutes les tables porteuses de données projet ont `project_id` et sont couvertes par
la RLS PostgreSQL (`SET app.current_org`), posée par la migration initiale.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    false,
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
    #: The identity provider's groups, as of the last sign-in through it — what an approval rule
    #: that names a group checks (ADR 0052). The IdP is authoritative: each sign-in replaces them.
    groups: Mapped[list[str]] = mapped_column(Json, default=list)


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
    #: Which channel of the agents this project runs when it pins nothing (S40-02, ADR 0071):
    #: `stable` (production, the default) or `candidate` (testing).
    agent_channel: Mapped[str] = mapped_column(String(16), default="stable", server_default="stable")

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
    #: Les secrets du connecteur, champ par champ, en RÉFÉRENCES (`env:NOM`) : résolues quand
    #: l'adaptateur est construit, jamais stockées en clair (ADR 0034).
    secret_refs: Mapped[dict[str, str] | None] = mapped_column(Json, nullable=True)
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
    #: What people granted on top of the ticket budget, in USD (S27-04, ADR 0047). Each grant is in
    #: the audit log (`workitem.budget_top_up`).
    budget_top_up_usd: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    #: The budget given at launch, or by the workflow's `budget.ticket_usd` (S27-06, ADR 0047). It
    #: only tightens the policy's ticket budget; `None`: the policy's applies.
    budget_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

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
    #: When the interpreter applied this run's outcome to its work item (#552): written with the
    #: state the decision moved it to. A run over but not decided is read back by a restarted
    #: interpreter; a decided one is a spent attempt, and a later pass plays the next one.
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tokens: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    # La dépense d'un run n'est lue qu'une fois au gateway : ce drapeau interdit le double comptage.
    spend_collected: Mapped[bool] = mapped_column(Boolean, default=False)
    gateway_key_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    stage_input: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    transcript_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: How the run's pod ended and the last lines of its log, redacted (S36-02, `RunLogArchive`).
    #: Written once, at the end of the run, by the orchestrator through the `Executor` protocol.
    log_archive: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
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
    # Who spent it (S29-01): the registry agent and version that ran, the workflow (name and
    # version) the work item was born in. NULL for a playbook run, or a line older than the columns
    # that no run could explain.
    agent_slug: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    agent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    workflow_name: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    workflow_version: Mapped[int | None] = mapped_column(Integer, nullable=True)


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


class MemoryStore(Base, PkMixin, TimestampMixin):
    """A named memory (S40-11, ADR 0075): `global` (the organisation's, curated by an administrator,
    with a reach) or `project` (a project's, curated by its owners, read by its runs only).

    The implicit memories of a project (`shared`, `own`) are never rows.
    """

    __tablename__ = "memory_stores"
    __table_args__ = (
        UniqueConstraint("org_id", "owner_key", "slug", name="uq_memory_stores_owner_slug"),
        CheckConstraint("(scope = 'project') = (project_id IS NOT NULL)", name="ck_memory_stores_scope"),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: global | project
    scope: Mapped[str] = mapped_column(String(16))
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    #: `org`, or the project's id: a unique key without NULLs.
    owner_key: Mapped[str] = mapped_column(String(36))
    #: For a global memory, whose runs may read it: `org` (every project) or `projects` (listed).
    reach: Mapped[str | None] = mapped_column(String(16), nullable=True)
    projects: Mapped[list[str]] = mapped_column(Json, default=list)
    #: active | archived
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)


class MemoryFact(Base, PkMixin, TimestampMixin):
    """Repli lexical : mémoire stockée dans Choregos quand Ecphoria n'est pas déployé.

    `embedding` est un `Json`, pas un vecteur : la similarité est un produit scalaire entre
    sacs de mots, calculé en Python. Le connecteur s'appelait `pgvector`, ce qui promettait
    autre chose."""

    __tablename__ = "memory_facts"
    __table_args__ = (
        Index("ix_memory_facts_project_subject", "project_id", "subject"),
        CheckConstraint(
            "project_id IS NOT NULL OR memory_store_id IS NOT NULL", name="ck_memory_facts_has_a_home"
        ),
    )

    #: The organisation the fact belongs to (S40-11): what the row-level security keys on, since a
    #: global memory's facts belong to no project.
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    #: `None` for the facts of a global memory (they carry `memory_store_id` only).
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=True
    )
    #: The named memory the fact is in (S40-11); `None` for a project's shared memory and for an
    #: agent's own memory.
    memory_store_id: Mapped[str | None] = mapped_column(
        ForeignKey("memory_stores.id", ondelete="CASCADE"), index=True, nullable=True
    )
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
    #: The agent whose own memory this is (S32-02, `memory: agent`); `None` for the project's
    #: shared memory. An agent's facts are read by that agent only, in this project only.
    agent_slug: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)


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


class OrgConnector(Base, PkMixin, TimestampMixin):
    """Une instance de connecteur de l'ORGANISATION (ADR 0034) : un annuaire Entra, un serveur MCP,
    un gestionnaire de parc — déclarée une fois par l'administrateur, nommée, et dont chaque
    opération porte sa politique. Les projets s'en servent ; ils n'en déclarent pas."""

    __tablename__ = "org_connectors"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_org_connectors_org_name"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))
    type: Mapped[str] = mapped_column(String(64))
    config: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    #: Des RÉFÉRENCES (`env:NOM`), jamais des valeurs.
    secret_refs: Mapped[dict[str, str] | None] = mapped_column(Json, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="unknown")
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: `<id>@<catalogue_version>` when installed from the MCP catalogue (ADR 0065); `NULL` otherwise.
    catalogue_entry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: `all_projects` (before ADR 0074: every project whose groups match) or `selected_projects`
    #: (only the projects with a row in `org_connector_projects`).
    availability: Mapped[str] = mapped_column(
        String(24), default="all_projects", server_default="all_projects"
    )


class OrgConnectorProject(Base, PkMixin, TimestampMixin):
    """A project an organisation connector is turned on for (ADR 0074): the project's own lock,
    read only when the connector's availability is `selected_projects`."""

    __tablename__ = "org_connector_projects"
    __table_args__ = (UniqueConstraint("connector_id", "project_id", name="uq_org_connector_projects"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    connector_id: Mapped[str] = mapped_column(ForeignKey("org_connectors.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    enabled_by: Mapped[str | None] = mapped_column(String(200), nullable=True)


class OrgSecret(Base, PkMixin, TimestampMixin):
    """A secret value typed in the console, sealed at rest (ADR 0058).

    Envelope encryption: the value is encrypted with a random data key (AES-256-GCM); the data key
    is wrapped under a key-encryption key (KEK) the DEPLOYMENT holds (`CHOREGOS_SECRETS_KEKS`), never
    the database. The additional authenticated data binds a row to its organisation, name and
    version, so it cannot be moved. No plaintext, no hash, no fingerprint: the hash of a
    low-entropy secret leaks it."""

    __tablename__ = "org_secrets"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_org_secrets_org_name"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    #: `connector/<name>/<field>` or `project/<slug>/<kind>/<field>`
    name: Mapped[str] = mapped_column(String(200))
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[bytes] = mapped_column(LargeBinary)
    wrapped_dek: Mapped[bytes] = mapped_column(LargeBinary)
    dek_nonce: Mapped[bytes] = mapped_column(LargeBinary)
    kek_id: Mapped[str] = mapped_column(String(32))
    algorithm: Mapped[str] = mapped_column(String(16), default="A256GCM")
    #: +1 at every set
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: The time of the last set.
    rotated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ModelCatalogueRow(Base, PkMixin, TimestampMixin):
    """One model of the organisation's catalogue (ADR 0060): what the organisation decided about a
    model the gateway routes — enabled or not, for which stage roles and usages, at which price —
    plus a snapshot of what the gateway said about it. Rows are never deleted by a sync: a model
    that leaves the gateway keeps its settings and gets `missing_since`."""

    __tablename__ = "model_catalogue"
    __table_args__ = (UniqueConstraint("org_id", "model_name", name="uq_model_catalogue_org_model"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    model_name: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(200), default="")
    source: Mapped[str] = mapped_column(String(16), default="gateway")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    #: Stage roles allowed to use it; empty = all.
    allowed_roles: Mapped[list[str]] = mapped_column(Json, default=list)
    usages: Mapped[list[str]] = mapped_column(Json, default=lambda: ["agent"])
    input_per_1k_override: Mapped[float | None] = mapped_column(Float, nullable=True)
    output_per_1k_override: Mapped[float | None] = mapped_column(Float, nullable=True)
    gateway_facts: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    missing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str | None] = mapped_column(String(200), nullable=True)


class RemoteCredential(Base, PkMixin, TimestampMixin):
    """What the organisation holds to call remote agents over A2A (ADR 0062).

    No secret lives here: the refresh token (rotated at every refresh), the DCR client secret and a
    bearer token are sealed values of the secret store (`remote/<name>/…`, ADR 0058); this row keeps
    the reference, the public client registration and the state."""

    __tablename__ = "remote_credentials"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_remote_credentials_org_name"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    #: `oauth2_authorization_code`, `bearer` or `none`.
    type: Mapped[str] = mapped_column(String(32))
    #: Public registration data: card URL, client id, token and authorisation endpoints, scopes,
    #: redirect URI. Never a secret.
    config: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    #: `store:remote/<name>/refresh_token` (OAuth) or `store:remote/<name>/token` (bearer); None before
    #: anyone authorised.
    secret_ref: Mapped[str | None] = mapped_column(String(240), nullable=True)
    authorised_as: Mapped[str | None] = mapped_column(String(320), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GitHubInstallation(Base, TimestampMixin):
    """An installation of the deployment's GitHub App on a GitHub account (ADR 0073).

    The primary key is GitHub's installation id, which nobody types: it is discovered by polling
    `GET /app/installations`, by the Setup URL redirect after an install, and by webhooks. `org_id`
    is the Choregos organisation that CLAIMED it; a token for `owner/repo` is only minted through an
    installation claimed by the project's organisation. Unclaimed rows are visible to the platform
    scope only."""

    __tablename__ = "github_installations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    account_login: Mapped[str] = mapped_column(String(200))
    #: `account_login` lower-cased: GitHub logins are case-insensitive, the lookups use this one.
    account_key: Mapped[str] = mapped_column(String(200), index=True)
    account_id: Mapped[int] = mapped_column(BigInteger)
    #: `Organization` or `User`
    account_type: Mapped[str] = mapped_column(String(32))
    app_id: Mapped[str] = mapped_column(String(32), default="")
    #: `all` or `selected`
    repository_selection: Mapped[str] = mapped_column(String(16), default="selected")
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    org_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    claimed_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NetworkDecision(Base, PkMixin, TimestampMixin):
    """What reached or was refused at the network, aggregated per run (ADR 0070).

    One row per (run, layer, host, port, decision, reason). The counters are CUMULATIVE totals sent
    by the reporter (the egress proxy, the runner): a replayed batch replaces them, it never adds."""

    __tablename__ = "network_decisions"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "layer", "host", "port", "decision", "reason", name="uq_network_decisions_key"
        ),
        Index("ix_network_decisions_org_last", "org_id", "last_at"),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    agent_slug: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: `proxy`, `runner`, `broker` or `platform`
    layer: Mapped[str] = mapped_column(String(16))
    host: Mapped[str] = mapped_column(String(253))
    port: Mapped[int] = mapped_column(Integer, default=0)
    #: `allowed`, `denied`, `rate_limited` or `unreachable`
    decision: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(200), default="")
    count: Mapped[int] = mapped_column(Integer, default=1)
    bytes_up: Mapped[int] = mapped_column(BigInteger, default=0)
    bytes_down: Mapped[int] = mapped_column(BigInteger, default=0)
    first_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OrgEgressRule(Base, PkMixin, TimestampMixin):
    """One host an organisation lets the platform call for it (S39-22, ADR 0066).

    `status` is `requested` (a member asked, nobody decided), `allowed`, `refused` or `revoked`;
    only `allowed` opens anything. At most one live (`requested` or `allowed`) row per host."""

    __tablename__ = "org_egress_rules"
    __table_args__ = (
        Index(
            "uq_org_egress_rules_live_host",
            "org_id",
            "host",
            unique=True,
            postgresql_where=text("status IN ('requested', 'allowed')"),
            sqlite_where=text("status IN ('requested', 'allowed')"),
        ),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    #: exact, or `*.` as the leftmost label only; lowercased and IDNA-encoded
    host: Mapped[str] = mapped_column(String(255))
    ports: Mapped[list[int]] = mapped_column(Json, default=lambda: [443])
    #: the pod families that make the call: a subset of `api`, `workers`, `runners`
    used_by: Mapped[list[str]] = mapped_column(Json, default=lambda: ["api"])
    status: Mapped[str] = mapped_column(String(16), default="requested")
    reason: Mapped[str] = mapped_column(String(500), default="")
    #: `mcp-catalogue:<id>@<version>`, `org-connector:<name>`, `skill-import:<repo>`, `manual`, `migration`...
    origin: Mapped[str] = mapped_column(String(200), default="manual")
    requested_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)


class ConnectorOperation(Base, PkMixin, TimestampMixin):
    """Une opération d'une instance, et sa politique dans l'organisation : `allowed`, `approval`
    (une action gouvernée, ADR 0035) ou `forbidden` ; les groupes de projets qui y ont droit
    (aucun : tous) ; son prix ; l'empreinte de son schéma (une dérive la referme, S19-03)."""

    __tablename__ = "connector_operations"
    __table_args__ = (
        UniqueConstraint("connector_id", "name", name="uq_connector_operations_connector_name"),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    connector_id: Mapped[str] = mapped_column(ForeignKey("org_connectors.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    access: Mapped[str] = mapped_column(String(8), default="read")
    policy: Mapped[str] = mapped_column(String(16), default="forbidden")
    groups: Mapped[list[str]] = mapped_column(Json, default=list)
    price_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    schema_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)
    #: Ce que le courtier annonce à l'agent, et vérifie avant de joindre le serveur.
    input_schema: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    #: The `outputSchema` the server declares: a successful result that does not match it is refused
    #: before the agent reads it (#755).
    output_schema: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The identity-provider group whose members approve the actions it proposes (ADR 0052).
    approver_group: Mapped[str | None] = mapped_column(String(200), nullable=True)


class ProjectOperationPolicy(Base, PkMixin, TimestampMixin):
    """Ce qu'un projet RESSERRE d'une opération de l'organisation — jamais ce qu'il élargit."""

    __tablename__ = "project_operation_policies"
    __table_args__ = (UniqueConstraint("project_id", "operation_id", name="uq_project_operation_policies"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    operation_id: Mapped[str] = mapped_column(
        ForeignKey("connector_operations.id", ondelete="CASCADE"), index=True
    )
    policy: Mapped[str] = mapped_column(String(16))


class Action(Base, PkMixin, TimestampMixin):
    """Une action gouvernée (ADR 0035) : proposée, décidée par des humains ré-authentifiés,
    exécutée par Temporal (`action-<id>`) — jamais dans la requête qui l'approuve. Ses effets
    sont consignés un à un (`action_effects`) : une reprise ne refait pas ce qui est fait."""

    __tablename__ = "actions"
    __table_args__ = (Index("ix_actions_project_status", "project_id", "status"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    work_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("work_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    origin: Mapped[str] = mapped_column(String(16))
    #: ce qu'elle fait, en un nom (`entra.create_user`, `arrivee.comptes`)
    kind: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(300))
    justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    params: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    #: [{effect, with, compensate?}] — joués dans l'ordre, compensés à rebours
    effects: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list)
    #: {kind: user|agent|system, id, via?}
    proposed_by: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    #: {approvers: [{role, min}], step_up_minutes, separation_of_duties}
    approval: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    decisions: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list)
    status: Mapped[str] = mapped_column(String(24), default="pending_approval")
    result: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    temporal_wf_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ActionEffect(Base, PkMixin, TimestampMixin):
    """Un effet d'une action, sous sa CLÉ (`<action>:<n>`) : consigné AVANT d'être tenté,
    confirmé après. Une activité rejouée voit la clé faite et ne refait rien ; un effet commencé
    et non confirmé (un worker tué) est retenté — les effets sont idempotents par construction."""

    __tablename__ = "action_effects"
    __table_args__ = (UniqueConstraint("action_id", "position", name="uq_action_effects_action_position"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    action_id: Mapped[str] = mapped_column(ForeignKey("actions.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    key: Mapped[str] = mapped_column(String(128), unique=True)
    effect: Mapped[str] = mapped_column(String(128))
    params: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    #: started | done | compensated | compensation_failed
    status: Mapped[str] = mapped_column(String(24), default="started")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HandOffReceipt(Base, PkMixin, TimestampMixin):
    """Un reçu de passage entre étapes (S25-04) : une sortie d'étape, son empreinte, et le run qui
    l'a PRODUITE ou LUE. Une ligne ne change jamais : une nouvelle révision fait un nouveau reçu,
    une lecture aussi. C'est ce qui montre que l'étape suivante a lu la révision exacte produite —
    et qu'une sortie modifiée après coup n'a plus l'empreinte de son reçu."""

    __tablename__ = "hand_off_receipts"
    __table_args__ = (
        UniqueConstraint(
            "work_item_id", "output", "digest", "run_id", "kind", name="uq_hand_off_receipts_passage"
        ),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    work_item_id: Mapped[str] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"), index=True)
    #: le nom de la sortie : `spec_markdown`, `plan_markdown`, ou celui qu'une transition déclare
    output: Mapped[str] = mapped_column(String(128))
    #: `sha256:<hex>` de la valeur, en JSON canonique (`services/recus.py`)
    digest: Mapped[str] = mapped_column(String(80))
    #: produced | read
    kind: Mapped[str] = mapped_column(String(16))
    run_id: Mapped[str] = mapped_column(String(64))
    #: le rôle de l'étape du run, gardé pour l'affichage : un run supprimé laisse son reçu lisible
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ArtifactRecord(Base, PkMixin, TimestampMixin):
    """One revision of a named output of a work item (ADR 0049): typed, stored, traced.

    An inline artifact (markdown, JSON) keeps its value in `content`; a file keeps its bytes in the
    object store under `storage_key`. Either way `digest` is the sha256 of the bytes served on
    download, recomputed on every read. A row never changes: a new content makes the next version.
    """

    __tablename__ = "artifacts"
    __table_args__ = (UniqueConstraint("work_item_id", "name", "version", name="uq_artifacts_version"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    work_item_id: Mapped[str] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer)
    #: a key of `choregos_contracts.ARTIFACT_TYPES`
    type: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(16))
    media_type: Mapped[str] = mapped_column(String(200))
    digest: Mapped[str] = mapped_column(String(80))
    size: Mapped[int] = mapped_column(Integer)
    content: Mapped[Any | None] = mapped_column(Json, nullable=True)
    storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    storage_url: Mapped[str | None] = mapped_column(String(700), nullable=True)
    #: who produced it: the run, its stage and attempt, the registry agent and its version
    run_id: Mapped[str] = mapped_column(String(128))
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    agent_slug: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)


class BudgetAlert(Base, PkMixin, TimestampMixin):
    """A budget crossed 80 % (or whatever the policy says) or 100 % (S29-04), shown in the inbox.

    One row per budget, period and threshold: the unique key is what makes the alert idempotent — a
    day's spend that stays above 80 % does not raise it again, and the day after is another period.
    """

    __tablename__ = "budget_alerts"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "scope",
            "subject",
            "period",
            "period_key",
            "threshold",
            name="uq_budget_alerts_threshold",
        ),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    #: `project` (its daily budget) or `agent` (its daily budget)
    scope: Mapped[str] = mapped_column(String(16))
    #: the project's or the agent's slug
    subject: Mapped[str] = mapped_column(String(64))
    #: `day`, or `month` (an agent's monthly cap, S27-09)
    period: Mapped[str] = mapped_column(String(8), default="day")
    #: `2026-10-13` for a day
    period_key: Mapped[str] = mapped_column(String(16))
    #: the percentage of the budget crossed: 80, 100
    threshold: Mapped[int] = mapped_column(Integer)
    spent_usd: Mapped[float] = mapped_column(Float, default=0.0)
    budget_usd: Mapped[float] = mapped_column(Float, default=0.0)
    #: when the alert reached Slack (monthly agent alerts only, S27-09); `None` = still to send
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: `open` until a person acknowledges it
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Agent(Base, PkMixin, TimestampMixin):
    """Un agent de l'organisation : interne (la plateforme le fait tourner) ou externe (un client
    de la porte MCP). Ce qu'il EST vit dans ses versions, immuables ; ici, son identité et son
    état — actif, suspendu, révoqué, expiré."""

    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("org_id", "slug", name="uq_agents_org_slug"),
        # A project agent has a project, and only a project agent has one (S39-09, ADR 0061).
        CheckConstraint("(scope = 'project') = (project_id IS NOT NULL)", name="scope_project"),
        CheckConstraint("candidate_percent BETWEEN 0 AND 100", name="candidate_percent"),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16), default="internal")
    #: Who owns the agent (ADR 0061): `platform` (a common agent installed from the catalogue), `org`,
    #: `project` (see `project_id`) or `personal` (see `owner_id`). The slug stays unique per organisation.
    scope: Mapped[str] = mapped_column(String(16), default="org", server_default="org", index=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    #: The agent this one was forked from, and the version it copied; the slug survives the source's deletion.
    forked_from_agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    forked_from_slug: Mapped[str | None] = mapped_column(String(64), nullable=True)
    forked_from_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    display_name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Labels people find the agent by (S40-10). Metadata, not part of a version: editing them never
    #: publishes one and never moves a checksum.
    tags: Mapped[list[str]] = mapped_column(Json, default=list, server_default="[]")
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Where the agent is in its life (S32-04): `draft` → `active` → `deprecated` → `retired`.
    #: Distinct from `status`, which is operational (active, suspended, revoked): a deprecated agent
    #: may be suspended, a retired one never runs again.
    lifecycle: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: The channels (S40-02, ADR 0071): each points at an immutable revision (`agent_versions.version`).
    #: `stable_version` is production; `candidate_version` is testing, served to the projects that
    #: follow it and to a share (`candidate_percent`, 0-100) of the other projects' work items.
    stable_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    candidate_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    candidate_percent: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    candidate_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: `AgentReleasePolicy` as JSON; NULL means the defaults.
    release_policy: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)


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
    #: Why this version exists, in the author's words (S40-12, ADR 0076); null for a version published
    #: before messages existed or through the original bare-spec endpoint.
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The version this one copies, when it is a restore (S40-12): the spec and checksum are the same bytes.
    restored_from: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ProjectAgent(Base, PkMixin, TimestampMixin):
    """L'épingle d'un projet sur une version d'agent, et ses surcharges — qui ne font que resserrer."""

    __tablename__ = "project_agents"
    __table_args__ = (
        UniqueConstraint("project_id", "agent_id", name="uq_project_agents_project_agent"),
        # A pin is a fixed revision XOR a channel (S40-02, ADR 0071).
        CheckConstraint("(version IS NULL) <> (channel IS NULL)", name="version_or_channel"),
    )

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    #: The fixed revision, or `None` when the pin follows a channel.
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: `stable` or `candidate` when the pin follows a channel, else `None`.
    channel: Mapped[str | None] = mapped_column(String(16), nullable=True)
    overrides: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)


class AgentEvaluation(Base, PkMixin, TimestampMixin):
    """A live evaluation of one revision (S40-02d): its live cases, started as capped trials. The outcome
    is read from the trial runs; this row says what was started, where, by whom and for how much."""

    __tablename__ = "agent_evaluations"

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    #: `not_run` when the deployment had no model to call; else read from the runs.
    status: Mapped[str] = mapped_column(String(16), default="running")
    budget_usd: Mapped[float] = mapped_column(Float, default=0.0)
    started_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: `[{name, work_item_id, run_id, budget_usd}]`, in the order of the suite.
    cases: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentRelease(Base, PkMixin, TimestampMixin):
    """One move of an agent's channels (S40-02, ADR 0071): the release history, the timeline of the
    lifecycle tab and the rollback oracle."""

    __tablename__ = "agent_releases"
    __table_args__ = (Index("ix_agent_releases_agent_created", "agent_id", "created_at"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    #: `candidate` or `stable`.
    channel: Mapped[str] = mapped_column(String(16))
    #: `publish`, `promote`, `rollback`, `withdraw` or `baseline`.
    kind: Mapped[str] = mapped_column(String(16))
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    previous_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="done")
    requested_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The checks as they were evaluated, a snapshot (`AgentReleaseCheck[]`).
    checks: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list)
    #: People other than the requester who must approve (S40-02b); 0 for a release that did not wait.
    approvals_required: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    #: What each approver answered (`AgentReleaseVote[]`), oldest first.
    decisions: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    """The files of a skill version (at most 256, 5 MiB in all) and their digest, which the runner
    checks before laying them down."""

    __tablename__ = "skill_versions"
    __table_args__ = (UniqueConstraint("skill_id", "version", name="uq_skill_versions_skill_version"),)

    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"), index=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    files: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    digest: Mapped[str] = mapped_column(String(80))
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: Where the version comes from when it was imported from git (S32-05): `repo`, `sha`, `path`,
    #: `licence`. `None` for a zip, the console or a template.
    source: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    #: The supply-chain gate (S32-06, ADR 0050): `low`, `medium` or `high`; `None` for a version
    #: published before the gate existed.
    risk: Mapped[str | None] = mapped_column(String(8), nullable=True)
    #: What the static scan and the licence check found: `{code, severity, detail, file, line}`.
    findings: Mapped[list[Any] | None] = mapped_column(Json, nullable=True)
    #: `approved`, or `pending_review` for a high-risk version until an administrator who is not its
    #: importer approves it. A pending version is pinned by no agent and reaches no run.
    review_status: Mapped[str] = mapped_column(String(16), default="approved", server_default="approved")
    reviewed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentCredential(Base, PkMixin, TimestampMixin):
    """Ce qui fait d'un client de la porte MCP un agent externe (ADR 0033) : un jeton `mcp:*` ou un
    client OAuth (`azp`), rattaché à un agent `external`. Ses droits sont alors ceux de l'humain,
    intersectés avec ceux de la version de l'agent ; sa révocation se relit à chaque appel."""

    __tablename__ = "agent_credentials"
    __table_args__ = (
        UniqueConstraint("api_token_id", name="uq_agent_credentials_api_token_id"),
        UniqueConstraint("org_id", "client_id", name="uq_agent_credentials_org_client"),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    api_token_id: Mapped[str | None] = mapped_column(
        ForeignKey("api_tokens.id", ondelete="CASCADE"), nullable=True
    )
    client_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ───────────────────────────── knowledge bases (ADR 0051) ─────────────────────────────


class KnowledgeBase(Base, PkMixin, TimestampMixin):
    """A curated reference corpus of the organisation, read-only for agents (ADR 0051).

    Not the memory: memory holds facts learned from work (proposed, then accepted); a knowledge
    base holds documents a human chose. `scope` says who may use it: `org` (every project),
    `projects` (the listed project slugs) or `groups` (the listed IdP groups, humans only).
    """

    __tablename__ = "knowledge_bases"
    __table_args__ = (UniqueConstraint("org_id", "slug", name="uq_knowledge_bases_org_slug"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: org | projects | groups
    scope: Mapped[str] = mapped_column(String(16), default="org")
    projects: Mapped[list[str]] = mapped_column(Json, default=list)
    groups: Mapped[list[str]] = mapped_column(Json, default=list)
    #: files (the converted corpus mounted in the workspace) | index (hybrid search)
    serving_mode: Mapped[str] = mapped_column(String(16), default="files")
    #: active | archived
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: questions and the documents (file names) expected to answer them (S33-03)
    eval_set: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list)
    #: the last run of the eval set: recall@5, per question, when, in which search mode
    eval_result: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    #: the last distillation into a principles skill (S33-05): status, skill, version, dropped, error
    distillation: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    #: the project this base was created from (S39-14): its owners curate it, never widen it
    owner_project: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: people read it only through a grant (or as an administrator or owning-project owner); runs
    #: that the base is attached to are still served (S42-11, ADR 0081)
    restricted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


class KnowledgeGrant(Base, PkMixin, TimestampMixin):
    """A role on one knowledge base given to a person, a group or a project (S42-11, ADR 0081).

    `subject_type`: `user` (`subject` is a user id, or an e-mail kept as typed for a person with no
    account yet), `group` (an IdP group name) or `project` (a project slug). `role`: viewer, editor
    or owner. A grant gives people a role; it never serves a run.
    """

    __tablename__ = "knowledge_grants"
    __table_args__ = (
        UniqueConstraint("knowledge_base_id", "subject_type", "subject", name="uq_knowledge_grants_subject"),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    subject_type: Mapped[str] = mapped_column(String(16))
    subject: Mapped[str] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(String(16))
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)


class KnowledgeDocument(Base, PkMixin, TimestampMixin):
    """One document of a knowledge base: its source bytes, kept for a re-conversion, and the
    markdown they became. `status`: pending → converting → ready | failed."""

    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint("knowledge_base_id", "digest", name="uq_knowledge_documents_kb_digest"),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    #: where it came from: `upload` today, a connector tomorrow (S33-04)
    source: Mapped[str] = mapped_column(String(32), default="upload")
    filename: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(300))
    mime: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer)
    #: sha256 of the source bytes: the same file twice is one document
    digest: Mapped[str] = mapped_column(String(80))
    #: the uploaded bytes, never loaded with a listing
    content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    markdown: Mapped[str | None] = mapped_column(Text, nullable=True, deferred=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    converted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: the connector source it was synced from (S33-04); none for an upload
    source_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"), nullable=True, index=True
    )
    #: its identity at the source (a Graph item id, a URL, a path) and its version there
    source_ref: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)


class KnowledgeSource(Base, PkMixin, TimestampMixin):
    """Where a knowledge base's documents are synced from (S33-04, ADR 0051): `sharepoint` (a site or
    drive through the organisation's `documents` connector, delta sync), `web` (one allow-listed
    site, crawled) or `git` (a repository's documentation). A document deleted at the source leaves
    the base and its index at the next sync."""

    __tablename__ = "knowledge_sources"

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(200))
    #: the org connector (`documents` capability) holding the credentials, for `sharepoint`
    connector: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    #: where the next sync starts: a Graph deltaLink, a commit
    cursor: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=60)
    #: interval | cron | manual (S39-19): what decides when the loop syncs
    refresh_mode: Mapped[str] = mapped_column(String(8), default="interval", server_default="interval")
    #: five fields, minute resolution, read in `refresh_timezone`; set in `cron` mode
    refresh_cron: Mapped[str | None] = mapped_column(String(100), nullable=True)
    refresh_timezone: Mapped[str] = mapped_column(String(64), default="UTC", server_default="UTC")
    #: when the source syncs next (an estimate: the worker may be down); null = due, or manual
    next_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: set by "sync now": a request later than `last_synced_at` makes the next pass sync
    sync_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: idle | syncing | failed
    status: Mapped[str] = mapped_column(String(16), default="idle")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: what the last sync did: added, updated, deleted, unchanged, skipped
    last_summary: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)


class KnowledgeDistillationPart(Base, PkMixin, TimestampMixin):
    """One model call of a distillation in passes, kept by the digest of what it read (S42-10c, ADR 0008).

    A part (`part_index >= 0`) holds the principles one slice of the corpus yielded; the merge
    (`part_index == -1`) holds the final list. The digest covers the model, the instructions and the
    text read, so a replayed activity, or a new distillation after one document changed, finds the
    answer instead of paying for it again.
    """

    __tablename__ = "knowledge_distillation_parts"
    __table_args__ = (
        UniqueConstraint("knowledge_base_id", "digest", name="uq_knowledge_distillation_parts"),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    digest: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(200))
    part_index: Mapped[int] = mapped_column(Integer)
    documents: Mapped[list[str]] = mapped_column(Json, default=list)
    characters: Mapped[int] = mapped_column(Integer, default=0)
    answer: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    #: done | failed
    status: Mapped[str] = mapped_column(String(16), default="done")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class KnowledgeChunk(Base, PkMixin, TimestampMixin):
    """A contextual chunk of a document of an `index` knowledge base (S33-03, ADR 0051).

    `text` is what is ranked: the document title and heading path, then the chunk. On PostgreSQL the
    migration adds what the ORM does not describe: a generated `search_tsv` (full-text, GIN) and,
    when pgvector is installed, `embedding_vec`. `embedding` (JSON) holds the vector only where
    there is no pgvector column to hold it (SQLite, development and tests).
    """

    __tablename__ = "knowledge_chunks"

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    heading_path: Mapped[list[str]] = mapped_column(Json, default=list)
    body: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Json, nullable=True, deferred=True)


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
    AgentCredential,
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeChunk,
    KnowledgeSource,
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
