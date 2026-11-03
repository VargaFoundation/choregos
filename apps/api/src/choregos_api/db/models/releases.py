# SPDX-License-Identifier: Apache-2.0
"""Releases and deployments."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, Json, PkMixin, TimestampMixin


class Release(Base, PkMixin, TimestampMixin):
    __tablename__ = "releases"
    __table_args__ = (Index("ix_releases_project_env", "project_id", "env"),)

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    env: Mapped[str] = mapped_column(String(32))
    batch_no: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="collecting", index=True)
    items: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    verdict: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    promotion_url: Mapped[str | None] = mapped_column(String(500), nullable=True)


class Deployment(Base, PkMixin, TimestampMixin):
    __tablename__ = "deployments"

    release_id: Mapped[str] = mapped_column(ForeignKey("releases.id", ondelete="CASCADE"), index=True)
    env: Mapped[str] = mapped_column(String(32))
    revision: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cd_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    analysis: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
