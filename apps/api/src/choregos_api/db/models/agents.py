# SPDX-License-Identifier: Apache-2.0
"""The agent registry (ADR 0033): agents, versions, project assignments, evaluations, releases."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, Json, PkMixin, TimestampMixin


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
