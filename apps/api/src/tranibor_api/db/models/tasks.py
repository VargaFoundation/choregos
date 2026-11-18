# SPDX-License-Identifier: Apache-2.0
"""A person's subscription to a task of an assistant (ADR 0137, S63-13)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, Json, PkMixin, TimestampMixin


class TaskSubscription(Base, PkMixin, TimestampMixin):
    """One person, one task of one installation. `trigger` is `{kind, cron, timezone}`: the cron is read
    in the subscriber's IANA time zone. `last_window` is the fire minute (UTC, ISO) the last run was
    claimed for, so a window runs once; `seen_keys` are the objects a `poll` already started a run for.
    `delegation_id` names the standing delegation (S63-14; no foreign key, a revoked one stays named)."""

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


class Delegation(Base, PkMixin, TimestampMixin):
    """A person's standing, read-only delegation of one task (ADR 0137 points 3-4, S63-14): what they
    agreed to (`needs`, with the operations), until when, and the digest of that agreement in the manifest
    installed then. `secret_ref` names the sealed offline token of the provider when a `user_exchange` need
    is read; it is never a column of a token. `status` is `active`, `suspended` (a condition does not hold
    now: `suspended_reason`) or `revoked`; the clock decides `expired` and is never stored."""

    __tablename__ = "delegations"
    __table_args__ = (Index("ix_delegations_member_task", "installation_id", "user_id", "task"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("assistant_installations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    task: Mapped[str] = mapped_column(String(63))
    needs: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    manifest_digest: Mapped[str] = mapped_column(String(64))
    secret_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    suspended_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: When the person was told it ends soon (`assistant_delegations.remind_expiring`): once per delegation.
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskEvent(Base, PkMixin):
    """The journal of the events routed to an installation's `trigger` tasks (S63-21, ADR 0137 point 2).

    One row per delivery that reached the door and was authentic, refused ones included, so that "why did
    nothing start?" has an answer. Never the payload and never the value that named the subscriber: the
    summary holds what the platform decided. `user_id` is the subscriber the event was routed to, or nothing
    (unmatched, ambiguous); it is cleared when that person is erased. `outcome`: `started`, `duplicate`
    or `refused`."""

    __tablename__ = "task_events"
    __table_args__ = (Index("ix_task_events_installation_received", "installation_id", "received_at"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("assistant_installations.id", ondelete="CASCADE"), index=True
    )
    task: Mapped[str] = mapped_column(String(63))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    event_id: Mapped[str] = mapped_column(String(255))
    outcome: Mapped[str] = mapped_column(String(16))
    summary: Mapped[str] = mapped_column(String(300), default="")
    work_item_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
