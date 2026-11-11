# SPDX-License-Identifier: Apache-2.0
"""Projects and what a project owns: connectors, workflow and policy definitions, templates."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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

from ..base import Base, Json, PkMixin, TimestampMixin

if TYPE_CHECKING:
    from .identity import Organization


#: A project of a team, or a person's personal space (ADR 0134).
PROJECT_KINDS = ("standard", "personal")


class Project(Base, PkMixin, TimestampMixin):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("org_id", "slug", name="org_slug"),
        # A personal space has its owner, a team's project has none (ADR 0134).
        CheckConstraint("(kind = 'personal') = (owner_user_id IS NOT NULL)", name="personal_has_owner"),
        # One personal space per person and organisation.
        Index(
            "uq_projects_personal_owner",
            "org_id",
            "owner_user_id",
            unique=True,
            postgresql_where=text("kind = 'personal'"),
            sqlite_where=text("kind = 'personal'"),
        ),
    )

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
    #: `standard` (a team's project) or `personal` (a person's space, ADR 0134): created at their first
    #: conversation, left out of every project listing, read by its owner only (RLS).
    kind: Mapped[str] = mapped_column(String(16), default="standard", server_default="standard")
    #: The owner of a personal space; none for a team's project.
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )

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
