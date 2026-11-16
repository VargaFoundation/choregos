# SPDX-License-Identifier: Apache-2.0
"""The launches of an assistant installation, bound to a project's workflow (ADR 0158, S66-22)."""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, PkMixin, TimestampMixin


class AssistantLaunchBinding(Base, PkMixin, TimestampMixin):
    """A launch of an installation, bound by reference to a project and the name of one of its workflows.

    Nothing of the workflow is copied: a confirmed proposal files its work item under the project's ACTIVE
    version of `workflow` at that moment. Deleting the project deletes the binding (the launch is then
    unbound, a readiness item when it is required)."""

    __tablename__ = "assistant_launch_bindings"
    __table_args__ = (
        UniqueConstraint("installation_id", "launch", name="uq_assistant_launch_bindings_launch"),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("assistant_installations.id", ondelete="CASCADE"), index=True
    )
    launch: Mapped[str] = mapped_column(String(63))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    workflow: Mapped[str] = mapped_column(String(64))
    #: False: an optional launch the owners switched off, or one an upgrade no longer lets fit.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    bound_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
