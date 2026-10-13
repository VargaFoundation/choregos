# SPDX-License-Identifier: Apache-2.0
"""The plugin's tables: ontology versions and the objects of `table` datasources (spec 10 §1.2).

They join the core's `Base`, hence `create_all()` (dev, tests); in a migrated database, the `ontology`
branch (`migrations/versions/`) creates them, with their RLS. Both belong to a project, hence to an
organisation, through `project_id`: the RLS policy is the one of the core's tables
(`choregos_project_org(project_id)`).

Deliberate departure from the spec: no `org_id` column (RLS derives it from the project, as the core
does), and only two statuses for a version (`active`, `superseded`) — the plan and the two-step apply
come with SOC-010.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from choregos_api.db.base import Base, Json, PkMixin, TimestampMixin, utcnow
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

ACTIVE = "active"
SUPERSEDED = "superseded"


class OntologyVersion(Base, PkMixin):
    __tablename__ = "ontology_versions"
    __table_args__ = (Index("ix_ontology_versions_project_status", "project_id", "status"),)

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(63))
    version: Mapped[str] = mapped_column(String(64))
    checksum: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(16), default=ACTIVE)
    compiled_ir: Mapped[dict[str, Any]] = mapped_column(Json)
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )


class ManagedObject(Base, TimestampMixin):
    """An object of a `table` datasource: its properties as JSON, under RLS (R-SOC-ONT-16)."""

    __tablename__ = "managed_objects"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    object_type: Mapped[str] = mapped_column(String(63), primary_key=True)
    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    properties: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


TABLES = (OntologyVersion, ManagedObject)
