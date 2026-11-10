# SPDX-License-Identifier: Apache-2.0
"""A person's subscription to a task of an assistant (ADR 0137, S63-13)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, Json, PkMixin, TimestampMixin


class TaskSubscription(Base, PkMixin, TimestampMixin):
    """One person, one task of one installation. `trigger` is `{kind, cron, timezone}`: the cron is read
    in the subscriber's IANA time zone. `last_window` is the fire minute (UTC, ISO) the last run was
    claimed for, so a window runs once; `seen_keys` are the objects a `poll` already started a run for.
    `delegation_id` names the standing delegation (S63-14, no foreign key until its table exists)."""

    __tablename__ = "task_subscriptions"
    __table_args__ = (UniqueConstraint("installation_id", "user_id", "task", name="uq_task_subscriptions"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("assistant_installations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    task: Mapped[str] = mapped_column(String(63))
    trigger: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    delegation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_window: Mapped[str | None] = mapped_column(String(32), nullable=True)
    seen_keys: Mapped[list[str]] = mapped_column(Json, default=list)
    #: Why the last scheduled or polled run was refused, or nothing.
    last_refusal: Mapped[str | None] = mapped_column(Text, nullable=True)
