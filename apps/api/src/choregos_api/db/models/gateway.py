# SPDX-License-Identifier: Apache-2.0
"""The model gateway: backend registry, gateway keys and the model catalogue."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, Json, PkMixin, TimestampMixin


class BackendRegistryRow(Base, PkMixin, TimestampMixin):
    """State of the ACP backends: enabled, conformance results (S2-11)."""

    __tablename__ = "agent_backends"

    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    capabilities: Mapped[list[str]] = mapped_column(Json, default=list)
    conformance: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    disabled_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class GatewayKeyRow(Base, PkMixin, TimestampMixin):
    __tablename__ = "gateway_keys"

    key_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    # Same length as `runs.id`: this column copies it without a foreign key (the key
    # outlives the run it capped).
    run_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    budget_usd: Mapped[float] = mapped_column(Float, default=0.0)
    spend_usd: Mapped[float] = mapped_column(Float, default=0.0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class ModelCatalogueRow(Base, PkMixin, TimestampMixin):
    """One model of the organisation's catalogue (ADR 0060): what the organisation decided about a
    model the gateway routes — enabled or not, for which stage roles and usages, at which price —
    plus a snapshot of what the gateway said about it. Rows are never deleted by a sync: a model
    that leaves the gateway keeps its settings and gets `missing_since`."""

    __tablename__ = "model_catalogue"
    __table_args__ = (UniqueConstraint("org_id", "model_name", name="uq_model_catalogue_org_model"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    model_name: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(200), default="")
    source: Mapped[str] = mapped_column(String(16), default="gateway")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    #: Stage roles allowed to use it; empty = all.
    allowed_roles: Mapped[list[str]] = mapped_column(Json, default=list)
    usages: Mapped[list[str]] = mapped_column(Json, default=lambda: ["agent"])
    input_per_1k_override: Mapped[float | None] = mapped_column(Float, nullable=True)
    output_per_1k_override: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: The most a voice session of the model may spend per minute it lasts (S41-15); null: no rate.
    max_usd_per_minute: Mapped[float | None] = mapped_column(Float, nullable=True)
    gateway_facts: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    missing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
