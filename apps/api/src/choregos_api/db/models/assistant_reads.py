# SPDX-License-Identifier: Apache-2.0
"""The reads journal of an assistant: one line per tool call of its conversations (S63-16, ADR 0133)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, PkMixin


class AssistantRead(Base, PkMixin):
    """What an assistant looked at, for whom and about whom: never the content it read.

    The conversation is not a foreign key: the journal outlives a conversation its person deleted or the
    purge took, since it is the accountability record of what the installation read."""

    __tablename__ = "assistant_reads"
    __table_args__ = (
        Index("ix_assistant_reads_installation_at", "installation_id", "at"),
        Index("ix_assistant_reads_subject", "installation_id", "subject_type", "subject_key"),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("assistant_installations.id", ondelete="CASCADE"), index=True
    )
    #: The person the read was made for (their user id, as `agent_sessions.user_id`).
    user_id: Mapped[str] = mapped_column(String(200), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    #: A task's work item (S63-13); null for a conversation.
    work_item_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: The task whose run or poll read (lot F2); null in a conversation.
    task: Mapped[str | None] = mapped_column(String(63), nullable=True)
    need: Mapped[str] = mapped_column(String(63))
    operation: Mapped[str] = mapped_column(String(200))
    subject_type: Mapped[str | None] = mapped_column(String(63), nullable=True)
    subject_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    #: The standing delegation a task read under (lot 2); null in a conversation.
    delegation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
