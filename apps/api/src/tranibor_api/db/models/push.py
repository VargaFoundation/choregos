# SPDX-License-Identifier: Apache-2.0
"""A person's devices that are told by Web Push (ADR 0194)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, Json, PkMixin, TimestampMixin


class PushSubscription(Base, PkMixin, TimestampMixin):
    """One browser or phone, registered by its person: where to send and what to encrypt for.

    Per PERSON, not per organisation: the device is the person's, and so are the organisations it
    hears from. Under RLS like `user_sessions` (ADR 0026): its owner reads and writes it, nobody
    else. The `endpoint` is a capability URL (whoever holds it can send to the device), so it is
    never returned by the API: the list shows `endpoint_host` only."""

    __tablename__ = "push_subscriptions"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    endpoint: Mapped[str] = mapped_column(String(2048), unique=True)
    p256dh: Mapped[str] = mapped_column(String(128))
    auth: Mapped[str] = mapped_column(String(64))
    device_name: Mapped[str] = mapped_column(String(80))
    #: The kinds this device wants; `None`: all of them.
    kinds: Mapped[list[str] | None] = mapped_column(Json, nullable=True)
    last_delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Since when it has failed without a success in between: past 30 days, the next failure deletes it.
    failing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
