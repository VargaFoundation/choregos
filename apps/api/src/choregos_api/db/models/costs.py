# SPDX-License-Identifier: Apache-2.0
"""The cost ledger and budget alerts."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, PkMixin, TimestampMixin


class CostLedger(Base, PkMixin):
    __tablename__ = "cost_ledger"
    __table_args__ = (
        Index("ix_cost_ledger_project_ts", "project_id", "ts"),
        Index("ix_cost_ledger_installation_id", "installation_id", "ts"),
    )

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
    #: The door a tool call came through (S57-06, ADR 0122 §4): `model` (the agent's MCP client),
    #: `script` (a script, through the runner's tool bridge) or `job` (a job step, read from the run).
    #: NULL on every other line (a model call, a distillation) and on a tool line booked before it.
    #: Attribution only: nothing that decides what a call may do reads it.
    origin: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: The SHA-256 of the script that made the call, when the runner's tool bridge named it.
    script_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The agent session that spent it (S41-09, ADR 0080 §10): no run, no work item.
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: The assistant installation and the person of a conversation's turn (S63-15): the month caps are
    #: summed on them, and they survive the deletion of the conversation, which detaches `session_id`.
    installation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


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
