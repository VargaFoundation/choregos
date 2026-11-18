# SPDX-License-Identifier: Apache-2.0
"""The skill library (ADR 0033)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, Json, PkMixin, TimestampMixin


class Skill(Base, PkMixin, TimestampMixin):
    """A skill of the organisation: a folder (`SKILL.md` and its files) that an agent carries. What
    it contains lives in its versions, which are immutable."""

    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("org_id", "slug", name="uq_skills_org_slug"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")


class SkillVersion(Base, PkMixin, TimestampMixin):
    """The files of a skill version (at most 256, 5 MiB in all) and their digest, which the runner
    checks before laying them down."""

    __tablename__ = "skill_versions"
    __table_args__ = (UniqueConstraint("skill_id", "version", name="uq_skill_versions_skill_version"),)

    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"), index=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    files: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    digest: Mapped[str] = mapped_column(String(80))
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: Where the version comes from when it was imported from git (S32-05): `repo`, `sha`, `path`,
    #: `licence`. `None` for a zip, the console or a template.
    source: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    #: The supply-chain gate (S32-06, ADR 0050): `low`, `medium` or `high`; `None` for a version
    #: published before the gate existed.
    risk: Mapped[str | None] = mapped_column(String(8), nullable=True)
    #: What the static scan and the licence check found: `{code, severity, detail, file, line}`.
    findings: Mapped[list[Any] | None] = mapped_column(Json, nullable=True)
    #: `approved`, or `pending_review` for a high-risk version until an administrator who is not its
    #: importer approves it. A pending version is pinned by no agent and reaches no run.
    review_status: Mapped[str] = mapped_column(String(16), default="approved", server_default="approved")
    reviewed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
