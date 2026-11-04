# SPDX-License-Identifier: Apache-2.0
"""The audit log and network decisions."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, Json, PkMixin, TimestampMixin


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
