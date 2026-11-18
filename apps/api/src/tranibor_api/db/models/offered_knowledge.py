# SPDX-License-Identifier: Apache-2.0
"""What an installation's offered knowledge remembers (S63-06, ADR 0138)."""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, PkMixin, TimestampMixin


class KnowledgeSeedTombstone(Base, PkMixin, TimestampMixin):
    """A seed document an owner deleted: an upgrade does not bring it back (S63-06, ADR 0138 point 3)."""

    __tablename__ = "knowledge_seed_tombstones"
    __table_args__ = (
        UniqueConstraint("knowledge_base_id", "path", name="uq_knowledge_seed_tombstones_base_path"),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(String(255))
    deleted_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
