# SPDX-License-Identifier: Apache-2.0
"""Work items, their runs and run events, human requests, events and findings."""

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

from ..base import Base, Json, PkMixin, TimestampMixin, uuid7


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
    #: How many times the work item came back after it ended (C1): its ticket reopened, its start label
    #: applied again, a trigger delivery for its key. Each comeback is a new pass of the same work item
    #: and the next run of the same interpreter id; the runs, events and decisions of the earlier passes
    #: stay where they are (`services/recurrence.py`).
    recurrence: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    #: When the current pass began: its attempts budget and its ticket spend count from there.
    recurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
