# SPDX-License-Identifier: Apache-2.0
"""Les tables du greffon : versions d'ontologie et objets des datasources `table` (spec 10 §1.2).

Elles s'inscrivent dans le `Base` du cœur, donc dans `create_all()` (dev, tests) ; en base migrée,
c'est la branche `ontology` (`migrations/versions/`) qui les crée, avec leur RLS. Les deux se
rattachent à un projet, donc à une organisation, par `project_id` : la politique RLS est celle des
tables du cœur (`choregos_project_org(project_id)`).

Écart assumé avec la spec : pas de colonne `org_id` (la RLS la déduit du projet, comme le cœur), et
deux statuts seulement pour une version (`active`, `superseded`) — le plan et l'application en
deux temps viendront avec SOC-010.
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
    """Un objet d'une datasource `table` : ses propriétés en JSON, sous RLS (R-SOC-ONT-16)."""

    __tablename__ = "managed_objects"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    object_type: Mapped[str] = mapped_column(String(63), primary_key=True)
    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    properties: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


TABLES = (OntologyVersion, ManagedObject)
