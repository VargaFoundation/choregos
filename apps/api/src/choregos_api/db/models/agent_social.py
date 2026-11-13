# SPDX-License-Identifier: Apache-2.0
"""What people do around an agent: watch it, star it, and what watching delivers to the inbox (ADR 0152)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, Json, PkMixin, TimestampMixin


class AgentWatch(Base, PkMixin, TimestampMixin):
    """A person follows an agent's evolutions. One row per person and agent; `level` is `all` (every
    event) or `releases` (versions and channels only: no fork, no lifecycle change)."""

    __tablename__ = "agent_watches"
    __table_args__ = (UniqueConstraint("agent_id", "user_id", name="uq_agent_watches_agent_user"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    level: Mapped[str] = mapped_column(String(16), default="all", server_default="all")


class AgentStar(Base, PkMixin, TimestampMixin):
    """A person's bookmark of an agent: the count is a signal of use, the flag is their shortlist."""

    __tablename__ = "agent_stars"
    __table_args__ = (UniqueConstraint("agent_id", "user_id", name="uq_agent_stars_agent_user"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)


class AgentNotification(Base, PkMixin, TimestampMixin):
    """One thing that happened to an agent, addressed to one watcher: an inbox item. Whether the
    watcher may still read the agent (and the fork it names) is decided when the item is read."""

    __tablename__ = "agent_notifications"
    __table_args__ = (Index("ix_agent_notifications_user_read", "user_id", "read_at"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    #: The fork a `forked` event names; the item is hidden from whoever may not see it.
    related_agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=True
    )
    #: `version_published`, `production_moved`, `candidate_started`, `candidate_promoted`,
    #: `candidate_withdrawn`, `rolled_back`, `forked`, `lifecycle_changed`.
    kind: Mapped[str] = mapped_column(String(24))
    actor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
