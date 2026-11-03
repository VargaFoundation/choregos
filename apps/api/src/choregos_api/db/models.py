# SPDX-License-Identifier: Apache-2.0
"""The data model (docs/plan/01 §1.3).

Every table that holds project data has `project_id` and is covered by PostgreSQL RLS
(`SET app.current_org`), set up by the initial migration.
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
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, Json, PkMixin, TimestampMixin, utcnow, uuid7


class Organization(Base, PkMixin, TimestampMixin):
    __tablename__ = "organizations"

    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    settings: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)

    projects: Mapped[list[Project]] = relationship(back_populates="org", cascade="all, delete-orphan")


class OrgSlugAlias(Base):
    """A slug an organisation had (ADR 0090). The organisation's id is its identity; its slug is an
    address, and an old address keeps resolving to it. It maps an address to an id and holds no content:
    outside RLS, since an address is resolved before any scope exists.
    The alias is unique across every alias, and never equal to a current slug (checked on write)."""

    __tablename__ = "org_slug_aliases"

    alias: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(320), nullable=True)


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
    #: `idp` when an IdP sign-in created the row from the provider's groups, `manual` otherwise
    #: (administrator, bootstrap, project creator); a manual row is never revoked by a sign-in.
    source: Mapped[str] = mapped_column(String(16), default="manual", server_default="manual")

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
    #: The name of the default workflow (ADR 0031): the one for a ticket that names none and that no
    #: routing rule selects. The default belongs to the project, not to a version.
    default_workflow: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The rules that choose the workflow of a ticket coming from a tracker (ADR 0031): an ordered
    #: list of `{when: {labels_any, labels_all, item_type}, workflow}`. Kept outside `config`, which
    #: `PATCH /projects/{id}` replaces as a whole.
    workflow_routing: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list)
    #: How a work item that names no workflow finds one (ADR 0116): `labels` (the rules alone),
    #: `rules_then_llm` (the rules, then a small model when none matched) or `llm` (the model alone).
    routing_strategy: Mapped[str] = mapped_column(String(16), default="labels", server_default="labels")
    #: What the model reads about each workflow beside its description: workflow name → hint.
    routing_hints: Mapped[dict[str, str]] = mapped_column(Json, default=dict)
    #: Below this confidence a decision model's choice is not taken and the default takes the item
    #: (ADR 0121); `None` keeps every answer. A chat model answers no confidence and is never held to it.
    routing_min_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
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
    #: The connector's secrets, field by field, as REFERENCES (`env:NAME`): resolved when the adapter
    #: is built, never stored in clear (ADR 0034).
    secret_refs: Mapped[dict[str, str] | None] = mapped_column(Json, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="unknown")
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped[Project] = relationship(back_populates="connectors")


class WorkflowDef(Base, PkMixin, TimestampMixin):
    __tablename__ = "workflow_defs"
    __table_args__ = (
        UniqueConstraint("project_id", "name", "version", name="project_name_version"),
        # ADR 0031: a project carries several workflows, but a single active version per name.
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
    #: For `source: catalogue`, the entry it was adopted from: `<source>/<name>@<version>` (ADR 0111).
    origin: Mapped[str | None] = mapped_column(String(200), nullable=True)
    yaml: Mapped[str] = mapped_column(Text)
    json_doc: Mapped[dict[str, Any]] = mapped_column("json", Json, default=dict)
    checksum: Mapped[str] = mapped_column(String(80), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    #: Who published this version: the version history shows it (ADR 0031).
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
    #: The VERSION of the workflow the ticket was born in (ADR 0031): publishing a new version never
    #: moves it; only `migrate` does, on purpose.
    workflow_def_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_defs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    policy_id: Mapped[str | None] = mapped_column(
        ForeignKey("policies.id", ondelete="SET NULL"), nullable=True
    )
    temporal_wf_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: Who filed the work item, as its tracker names them (Jira: the reporter, `Name <e-mail>`; GitHub,
    #: GitLab: the author's login; the console: the person's e-mail). Written once at birth (#1179):
    #: an agent whose rules depend on the requester reads it. Personal data, never in a redacted entry.
    reporter: Mapped[str | None] = mapped_column(String(320), nullable=True)
    #: Who started the work item, as `<kind>:<subject>` (`services/initiator.py`, ADR 0077 §2):
    #: written once at birth by the door it came through; `None` for a work item born before S40-22.
    initiated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: The restricted knowledge bases its runs were served (ADR 0114): only ever added to; reading the
    #: work item and everything under it needs reading every one of them (`knowledge/marked.py`).
    restricted_knowledge: Mapped[list[str] | None] = mapped_column(Json, nullable=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Why the ticket's interpreter died (`{message, activity, at}`), or nothing.
    #: Set by the workflow itself as it dies, cleared when it restarts.
    failure: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    allowed_paths: Mapped[list[str]] = mapped_column(Json, default=list)
    #: The ticket's fields, validated at birth by `metadata.inputs` of its workflow (ADR 0031).
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

    # A run's identifier is NOT a UUID: it is deterministic and readable,
    # `<ticket>-<transition>-<attempt>` (activities/stage.py), and it is what makes
    # the stage replayable without creating a second run. The 36 characters inherited from `PkMixin`
    # are not enough: PostgreSQL answers "value too long for type character
    # varying(36)", where SQLite, which does not enforce lengths, says nothing.
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
    #: The registry agent that made this run, and its version (ADR 0033); empty for a playbook.
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
    # A run's spend is read from the gateway only once: this flag prevents double counting.
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
    #: The run's chain (ADR 0077 §2), written at stage start by the orchestrator, never by the agent:
    #: its work item's initiator, and the runs whose outputs the step reads as declared inputs (the
    #: latest `produced` receipt of each). `None` on both for a run started before S40-22.
    initiated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    inputs_from: Mapped[list[str] | None] = mapped_column(Json, nullable=True)

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

    #: The project that spent it. NULL for an organisation-level line (S42-10e, ADR 0092: a
    #: distillation of a base with no owning project): then `org_id` is set.
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    org_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    work_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("work_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # `model`, `tool` or `distill` (S42-10e): one ledger carries what a model call, a catalogue tool
    # call and a knowledge distillation cost. Without this column, the per-model totals would count
    # tools as models.
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
    #: The agent session that spent it (S41-09, ADR 0080 §10): no run, no work item.
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )


class AuditLog(Base, PkMixin):
    """The audit log. `org_id` is NULL for a platform event (a sign-in, a
    token): those rows are visible only to a session with scope `*`. Without this column,
    the table was outside RLS and the route filtered nothing: a `project_owner` of one
    organisation read the audit of all the others."""

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
    #: An MCP token can be bound to a project: it then sees only that one (ADR 0030).
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    #: The client of the last call (`User-Agent`, truncated): the Integrations page says "connected".
    last_client: Mapped[str | None] = mapped_column(String(200), nullable=True)


class TrackerIdentity(Base, PkMixin, TimestampMixin):
    """A member's account on a tracker, by the provider's stable numeric user id (S47-14, ADR 0102).

    Linked by an organisation administrator (not proven by the member). A login or a display name is
    never matched: only `external_id`. `login` is shown, nothing more."""

    __tablename__ = "tracker_identities"
    __table_args__ = (
        UniqueConstraint("org_id", "tracker", "external_id", name="uq_tracker_identities_external"),
        UniqueConstraint("org_id", "user_id", "tracker", name="uq_tracker_identities_member"),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    tracker: Mapped[str] = mapped_column(String(16))
    external_id: Mapped[str] = mapped_column(String(64))
    login: Mapped[str] = mapped_column(String(255))
    created_by: Mapped[str | None] = mapped_column(String(320), nullable=True)


class A2aPushConfig(Base, PkMixin, TimestampMixin):
    """A URL an A2A client registered to hear of a task (ADR 0078 §5, S40-23d).

    Bound to the `chg_` token that registered it: deleted with it, and read again before each
    delivery. The signing secret and the client's own token are sealed in the secret store under
    `a2a/push/<id>`, never here. `cursor_*` is the last event delivered: a delivery that fails
    leaves it, and the next pass sends again (at least once)."""

    __tablename__ = "a2a_push_configs"
    __table_args__ = (
        UniqueConstraint("work_item_id", "client", "config_id", name="uq_a2a_push_configs_client_config"),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    work_item_id: Mapped[str] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"), index=True)
    api_token_id: Mapped[str] = mapped_column(ForeignKey("api_tokens.id", ondelete="CASCADE"), index=True)
    #: `jeton:<id>`, as the door's rate limit and audit name the client.
    client: Mapped[str] = mapped_column(String(255))
    #: The client's id for this registration (A2A `pushNotificationConfig.id`).
    config_id: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(Text)
    cursor_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cursor_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    #: What the last delivery said (A2A state, workflow state, pending request): an event that
    #: changes none of it moves the cursor without a call.
    last_view: Mapped[str | None] = mapped_column(String(400), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(400), nullable=True)
    #: Set once the final state is delivered, or the registration given up: no more deliveries.
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    """Lexical fallback: memory stored in Choregos when Ecphoria is not deployed.

    `embedding` is a `Json`, not a vector: the similarity is a dot product between
    bags of words, computed in Python. The connector used to be called `pgvector`, which promised
    something else."""

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
    """Webhook deduplication: a delivery is processed only once."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (UniqueConstraint("source", "delivery_id", name="source_delivery"),)

    source: Mapped[str] = mapped_column(String(32), index=True)
    delivery_id: Mapped[str] = mapped_column(String(255))
    event_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)


class ReceivedEvent(Base, PkMixin):
    """A webhook event a project received, and what the platform did with it (S42-06f).

    One row per event of a verified delivery that reached a project — never the payload, a header, a
    comment or an email: the summary is built from typed fields. `webhook_deliveries` stays the
    deduplication table (one row per delivery, written before the outcome is known)."""

    __tablename__ = "received_events"
    __table_args__ = (Index("ix_received_events_project_received", "project_id", "received_at"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    work_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("work_items.id", ondelete="SET NULL"), nullable=True
    )
    work_item_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: `jira`, `github`, `gitlab`, `tekton`, `argocd`, `alertmanager`, `plugin:<plugin>/<server>`,
    #: `trigger:<name>` (S56-01), `schedule:<name>`
    source: Mapped[str] = mapped_column(String(160))
    event_type: Mapped[str] = mapped_column(String(80))
    delivery_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: `created_work_item`, `started`, `signalled`, `boarded`, `labels_updated`,
    #: `ignored_no_item_key`, `ignored_not_agent_ready`, `already_open` or `refused` (S56-01)
    outcome: Mapped[str] = mapped_column(String(32))
    summary: Mapped[str] = mapped_column(String(300), default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TriggerToken(Base, PkMixin):
    """The token of a project's HTTP trigger (S56-01, ADR 0113): its SHA-256, never the token.

    The trigger itself is declared in the project's configuration (`triggers:`); this row is what a
    caller must prove. Minting a new one replaces the old: one token per trigger, shown once."""

    __tablename__ = "trigger_tokens"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_trigger_tokens_project_name"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(63))
    token_sha256: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DecisionRecordRow(Base, PkMixin):
    """One answered question and what followed it: the decision ledger (ADR 0121 §6, S52-05).

    A decision step's answer (`source = decision_model`) or the confidence an agent declared
    (`source = agent`), one row per run and question, written once when the run's result is written.
    `transition` is filled when the workflow selects its next transition; the outcome fields when they
    become known — a person's verdict (`overridden`), the work item's end (`terminal`), its return
    (`reopened`). Calibration is computed from these rows (`services.decision_ledger`)."""

    __tablename__ = "decision_records"
    __table_args__ = (
        UniqueConstraint("run_id", "question", name="uq_decision_records_run_question"),
        Index("ix_decision_records_step", "project_id", "workflow", "step"),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    work_item_id: Mapped[str] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    #: The work item's tracker key and the project's slug, as the contract names them.
    work_item_key: Mapped[str] = mapped_column(String(255))
    project_slug: Mapped[str] = mapped_column(String(64))
    workflow: Mapped[str] = mapped_column(String(100))
    workflow_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The state the step ran from, and the step's transition.
    step: Mapped[str] = mapped_column(String(64))
    transition_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    question: Mapped[str] = mapped_column(String(64))
    #: `decision_model` or `agent`.
    source: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(200))
    model_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: A boolean, a number or a short text, kept as JSON.
    answer: Mapped[Any] = mapped_column(Json)
    probability: Mapped[float] = mapped_column(Float)
    #: The state the workflow went to after the step, once known.
    transition: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    overridden: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    #: `success`, `declined` or `cancelled`.
    terminal: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reopened: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BackendRegistryRow(Base, PkMixin, TimestampMixin):
    """State of the ACP backends: enabled, conformance results (S2-11)."""

    __tablename__ = "agent_backends"

    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    capabilities: Mapped[list[str]] = mapped_column(Json, default=list)
    conformance: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    disabled_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class GatewayKeyRow(Base, PkMixin, TimestampMixin):
    __tablename__ = "gateway_keys"

    key_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    # Same length as `runs.id`: this column copies it without a foreign key (the key
    # outlives the run it capped).
    run_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    budget_usd: Mapped[float] = mapped_column(Float, default=0.0)
    spend_usd: Mapped[float] = mapped_column(Float, default=0.0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


# ───────────────────────────── the agent registry (ADR 0033) ─────────────────────────────


class OrgConnector(Base, PkMixin, TimestampMixin):
    """A connector instance of the ORGANISATION (ADR 0034): an Entra directory, an MCP server,
    a device manager. Declared once by the administrator, named, and each of its
    operations carries its policy. Projects use it; they do not declare it."""

    __tablename__ = "org_connectors"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_org_connectors_org_name"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))
    type: Mapped[str] = mapped_column(String(64))
    config: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    #: REFERENCES (`env:NAME`), never values.
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
    #: The server a plugin brings, when the connector is one (ADR 0096): its declaration as the
    #: installer wrote it (image, port, paths, `config_schema`, `secret_fields`, `egress`, the declared
    #: `operations`, the Service URL). Written by the installer only, never by the connector routes:
    #: the broker reads its operations as the governance floor. `NULL` for any other connector.
    plugin_server: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)


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
    #: The most a voice session of the model may spend per minute it lasts (S41-15); null: no rate.
    max_usd_per_minute: Mapped[float | None] = mapped_column(Float, nullable=True)
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
    """An operation of an instance, and its policy in the organisation: `allowed`, `approval`
    (a governed action, ADR 0035) or `forbidden`; the project groups entitled to it
    (none: all); its price; the digest of its schema (a drift closes it again, S19-03)."""

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
    #: What the broker announces to the agent, and checks before reaching the server.
    input_schema: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    #: The `outputSchema` the server declares: a successful result that does not match it is refused
    #: before the agent reads it (#755).
    output_schema: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The identity-provider group whose members approve the actions it proposes (ADR 0052).
    approver_group: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: The most one of its results may weigh when it reaches the agent; none: the platform's default
    #: (S40-17). Never above the platform's ceiling.
    result_max_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ProjectOperationPolicy(Base, PkMixin, TimestampMixin):
    """What a project NARROWS of an organisation operation, never what it widens."""

    __tablename__ = "project_operation_policies"
    __table_args__ = (UniqueConstraint("project_id", "operation_id", name="uq_project_operation_policies"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    operation_id: Mapped[str] = mapped_column(
        ForeignKey("connector_operations.id", ondelete="CASCADE"), index=True
    )
    policy: Mapped[str] = mapped_column(String(16))


class Action(Base, PkMixin, TimestampMixin):
    """A governed action (ADR 0035): proposed, decided by re-authenticated humans,
    executed by Temporal (`action-<id>`), never inside the request that approves it. Its effects
    are recorded one by one (`action_effects`): a resumption does not redo what is done."""

    __tablename__ = "actions"
    __table_args__ = (Index("ix_actions_project_status", "project_id", "status"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    work_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("work_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    origin: Mapped[str] = mapped_column(String(16))
    #: what it does, as a name (`entra.create_user`, `arrivee.comptes`)  # lang: fr-ok - a recorded kind
    kind: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(300))
    justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    params: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    #: [{effect, with, compensate?}]: played in order, compensated in reverse
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
    """An effect of an action, under its KEY (`<action>:<n>`): recorded BEFORE it is attempted,
    confirmed after. A replayed activity sees the key done and redoes nothing; an effect started
    and not confirmed (a killed worker) is retried: effects are idempotent by construction."""

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
    """A receipt for a hand-off between stages (S25-04): a stage output, its digest, and the run that
    PRODUCED or READ it. A row never changes: a new revision makes a new receipt, and so does
    a read. This is what shows that the next stage read the exact revision produced,
    and that an output modified afterwards no longer has the digest of its receipt."""

    __tablename__ = "hand_off_receipts"
    __table_args__ = (
        UniqueConstraint(
            "work_item_id", "output", "digest", "run_id", "kind", name="uq_hand_off_receipts_passage"
        ),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    work_item_id: Mapped[str] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"), index=True)
    #: the name of the output: `spec_markdown`, `plan_markdown`, or the one a transition declares
    output: Mapped[str] = mapped_column(String(128))
    #: `sha256:<hex>` of the value, in canonical JSON (`services/recus.py`)
    digest: Mapped[str] = mapped_column(String(80))
    #: produced | read
    kind: Mapped[str] = mapped_column(String(16))
    run_id: Mapped[str] = mapped_column(String(64))
    #: the role of the run's stage, kept for display: a deleted run leaves its receipt readable
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
    """An agent of the organisation: internal (the platform runs it) or external (a client
    of the MCP door). What it IS lives in its versions, which are immutable; here, its identity and its
    state: active, suspended, revoked, expired."""

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
    """An agent version: instructions, model, limits, skills, MCP servers, budget. It is never
    modified: a change publishes the next one."""

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


class HostedBuildRecord(Base, PkMixin, TimestampMixin):
    """The build of a hosted agent's zip (ADR 0080 §9), one per organisation and build key: every
    version with the same inputs shares it, and the versions themselves stay as published."""

    __tablename__ = "hosted_builds"
    __table_args__ = (UniqueConstraint("org_id", "key", name="uq_hosted_builds_org_key"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(80))
    #: `pending`, `building`, `ready` or `failed`.
    status: Mapped[str] = mapped_column(String(16), default="pending")
    #: Which build of this key: a failed build published again is attempt 2, under its own workflow.
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    image: Mapped[str | None] = mapped_column(String(400), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    provenance: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentSessionRecord(Base, PkMixin, TimestampMixin):
    """A person talking to an agent directly (S41-09, ADR 0080 §10), next to runs: no work item, no
    workflow. Its gateway key is minted once, when it opens, sealed in the secret store, and revoked
    when it closes; its transcript is `agent_session_events`."""

    __tablename__ = "agent_sessions"

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    agent_slug: Mapped[str] = mapped_column(String(64))
    agent_version: Mapped[int] = mapped_column(Integer)
    #: Who opened it: the only person who may talk in it, read it or close it.
    user_id: Mapped[str] = mapped_column(String(200), index=True)
    #: `open`, `closed` (by its person or at its expiry) or `failed` (the budget, the model).
    status: Mapped[str] = mapped_column(String(16), default="open")
    model: Mapped[str] = mapped_column(String(200), default="")
    gateway_key_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: The chat history the next response continues, the system message first.
    messages: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list)
    responses: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Why it closed: `closed`, `expired`, `idle`, `budget_exceeded`, `model_error`, `pod_failed`.
    reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: A hosted session's pod (S41-11), as its workflow last wrote it: `{state, kind, name,
    #: namespace}`, `state` one of `requested`, `running`, `stopped`. Null before the first response.
    pod: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    #: What a hosted session's runner reads at start (`GET /internal/agent-sessions/{id}/input`): no
    #: gateway key, no token — those reach the pod through its Secret only.
    stage_input: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)


class AgentSessionEvent(Base, PkMixin):
    """One event of a session's transcript, in order: what the person said, each model turn, each
    tool call or refusal, how each response ended. Text only: audio is never journaled."""

    __tablename__ = "agent_session_events"
    __table_args__ = (UniqueConstraint("session_id", "seq", name="uq_agent_session_events_seq"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("agent_sessions.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProjectAgent(Base, PkMixin, TimestampMixin):
    """A project's pin on an agent version, and its overrides, which can only narrow."""

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


# ───────────────────────────── the skill library (ADR 0033) ─────────────────────────────


class Skill(Base, PkMixin, TimestampMixin):
    """A skill of the organisation: a folder (`SKILL.md` and its files) that an agent carries. What
    it contains lives in its versions, which are immutable."""

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
    """What makes a client of the MCP door an external agent (ADR 0033): an `mcp:*` token or an
    OAuth client (`azp`), attached to an `external` agent. Its rights are then those of the human,
    intersected with those of the agent version; its revocation is re-read on every call."""

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
    #: the project this base was created from (S39-14): its owners curate it and widen its reach
    owner_project: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: people read it only through a grant (or as an administrator or owning-project owner); a run
    #: is served it only on behalf of a person who reads it (S42-11e, ADR 0097)
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
    #: what a `ready` conversion left out, one sentence each (S42-09e): a spreadsheet cut at its row cap
    warnings: Mapped[list[str]] = mapped_column(Json, default=list, server_default="[]")
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


class OrgPlugin(Base, PkMixin, TimestampMixin):
    """A plugin installed in an organisation (ADR 0093, S43-04): which version, from which source, its
    content digest, and what it brought, by the identifier each item has in the organisation.

    `status`: `installed`, or `servers_pending` when the plugin declares servers the platform does not
    run yet (S43-06). `details` holds what the installer needs again: the template directories by name,
    the procedure files by id, the server documents, and what it skipped because the organisation
    already had an object of that name.
    """

    __tablename__ = "org_plugins"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_org_plugins_org_name"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(128))
    digest: Mapped[str] = mapped_column(String(80))
    manifest: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    contents: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    details: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="installed")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    #: Who installed this version; `None` for the start-up reconcile (S43-05).
    installed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OrgWorkflow(Base, PkMixin, TimestampMixin):
    """A workflow an organisation shared to its catalogue (ADR 0111 §4): a copy of one version of a
    project's workflow, so later edits in the project do not reach it until shared again. Each share of
    the same name is the next `version`; the catalogue offers the highest."""

    __tablename__ = "org_workflows"
    __table_args__ = (
        UniqueConstraint("org_id", "name", "version", name="uq_org_workflows_org_name_version"),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    yaml: Mapped[str] = mapped_column(Text)
    checksum: Mapped[str] = mapped_column(String(80))
    #: `<project>/<workflow>@<version>` it was copied from.
    shared_from: Mapped[str] = mapped_column(String(200))
    shared_by: Mapped[str | None] = mapped_column(String(200), nullable=True)


class OrgPluginFile(Base, PkMixin, TimestampMixin):
    """A file of an installed plugin that the platform serves again later: its templates (with their
    files), its eval suites and its procedures. Keyed by the plugin's content digest, so an upgrade
    writes the new digest's files before it deletes the old ones; under RLS like the plugin row, so a
    private plugin's text is not readable from another organisation."""

    __tablename__ = "org_plugin_files"
    __table_args__ = (
        UniqueConstraint("org_id", "digest", "path", name="uq_org_plugin_files_org_digest_path"),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    digest: Mapped[str] = mapped_column(String(80))
    path: Mapped[str] = mapped_column(String(500))
    content: Mapped[bytes] = mapped_column(LargeBinary)


class OrgPluginApproval(Base, PkMixin, TimestampMixin):
    """A plugin install or upgrade waiting for a second person (ADR 0093 §4, ADR 0050, S43-05).

    A plugin that brings a server is not installed on one administrator's word: the request is kept
    here, at the exact version and content digest that was checked, and nothing is written until an
    administrator other than `requested_by` approves it. `requested_by` is `None` when the start-up
    reconcile asked for a default plugin (`is_default`): any administrator of the organisation may then
    approve. `status`: `pending`, then `approved`.
    """

    __tablename__ = "org_plugin_approvals"

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    #: `install` or `upgrade`
    action: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(128))
    digest: Mapped[str] = mapped_column(String(80))
    reason: Mapped[str] = mapped_column(Text, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    requested_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    decided_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    OrgPlugin,
    OrgPluginFile,
    OrgPluginApproval,
]


def is_core_object(obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any) -> bool:
    """Alembic's `include_object` filter: the core migrations describe only THEIR tables.

    An extension registers its models in the same `Base`, which is what puts them in `create_all()`,
    and creates its tables through its own migration branch (`choregos.migrations`). Without this
    filter, an autogeneration run with an extension installed would put its tables in a core
    migration, and the models/migrations comparison of the core would see them as missing. A table
    belongs to the core if its class is defined in `choregos_api`; an extension's tables do not.
    """
    if type_ != "table":
        return True
    return name in {
        mapper.class_.__tablename__
        for mapper in Base.registry.mappers
        if mapper.class_.__module__.startswith("choregos_api.")
    }


objet_du_coeur = is_core_object  # lang: fr-ok alias:internal
